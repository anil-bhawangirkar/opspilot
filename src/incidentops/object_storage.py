"""Immutable artifact adapters with tenant-scoped, content-addressed references.

The caller redacts/validates source artifacts before persistence. This module
preserves exact bytes and does not reinterpret JSON or invent an ETag checksum.
The S3 adapter requires bucket versioning; configure IAM/retention separately.
"""

import asyncio
import base64
import hashlib
import os
import re
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

_TENANT = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
_T = TypeVar("_T")


class ObjectAccessError(PermissionError):
    """The reference or namespace is outside the requested tenant's scope."""


class ObjectIntegrityError(ValueError):
    """Persisted bytes or version identity do not match the immutable reference."""


class ObjectRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}/[0-9a-f]{64}\.json$")
    version_id: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class ObjectStore(Protocol):
    async def put(self, tenant_id: str, payload: bytes) -> ObjectRef: ...

    async def get(self, tenant_id: str, ref: ObjectRef) -> bytes: ...

    async def aclose(self) -> None: ...


def _tenant(tenant_id: str) -> None:
    if not isinstance(tenant_id, str) or not _TENANT.fullmatch(tenant_id):
        raise ObjectAccessError("Invalid tenant namespace")


def _key(tenant_id: str, digest: str) -> str:
    return f"{tenant_id}/{digest}.json"


def _check_ref(tenant_id: str, ref: ObjectRef, max_bytes: int) -> None:
    _tenant(tenant_id)
    # Recheck even an ObjectRef: model_copy/model_construct bypass Pydantic validation.
    if not re.fullmatch(r"[0-9a-f]{64}", ref.sha256) or ref.key != _key(tenant_id, ref.sha256):
        raise ObjectAccessError("Object reference is outside tenant scope")
    if ref.size_bytes < 0 or ref.size_bytes > max_bytes:
        raise ObjectIntegrityError("Object reference exceeds size limit")


def _verify(payload: bytes, digest: str, size: int) -> bytes:
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
        raise ObjectIntegrityError("Object bytes do not match checksum/size")
    return payload


class _ThreadedStore:
    """Bound blocking IO without releasing a permit when its awaiting caller cancels.

    Cancellation cannot stop boto3 or a filesystem syscall already in a thread.
    Track that work until it finishes; aclose drains it before closing resources.
    Instances belong to one event loop and are not reusable after aclose.
    """

    def __init__(self, max_bytes: int, concurrency: int):
        if max_bytes <= 0 or concurrency <= 0:
            raise ValueError("Object size/concurrency limits must be positive")
        self.max_bytes = max_bytes
        self._permits = asyncio.Semaphore(concurrency)
        self._pending: set[asyncio.Task] = set()
        self._closed = False

    async def _run(self, operation: Callable[..., _T], *args: Any) -> _T:
        if self._closed:
            raise RuntimeError("Object store is closed")
        await self._permits.acquire()
        if self._closed:
            self._permits.release()
            raise RuntimeError("Object store is closed")
        task = asyncio.create_task(asyncio.to_thread(operation, *args))
        self._pending.add(task)

        def finished(completed):
            self._pending.discard(completed)
            self._permits.release()
            # Retrieve a background exception if its original awaiter was cancelled.
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    def _payload(self, tenant_id: str, payload: bytes) -> tuple[str, str]:
        _tenant(tenant_id)
        if not isinstance(payload, bytes):
            raise TypeError("Object payload must be bytes")
        if len(payload) > self.max_bytes:
            raise ValueError("Object payload exceeds size limit")
        digest = hashlib.sha256(payload).hexdigest()
        return _key(tenant_id, digest), digest

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        pass


class LocalObjectStore(_ThreadedStore):
    """Local lab adapter: atomic no-overwrite publication, fsync and no symlink traversal.

    Descendant access uses a pinned root directory descriptor. The configured root
    and its parents must be controlled by the operator, not uploaded by a tenant.
    """

    def __init__(self, root: str | Path, *, max_bytes: int = 5 * 1024 * 1024, concurrency: int = 4):
        super().__init__(max_bytes, concurrency)
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def _tenant_fd(self, tenant_id: str, *, create: bool = False) -> int:
        if create:
            try:
                os.mkdir(tenant_id, mode=0o700, dir_fd=self._root_fd)
                os.fsync(self._root_fd)
            except FileExistsError:
                pass
        try:
            return os.open(
                tenant_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._root_fd
            )
        except OSError as exc:
            if isinstance(exc, FileNotFoundError):
                raise
            raise ObjectAccessError("Tenant object directory is not a safe directory") from exc

    def _read(self, tenant_fd: int, filename: str) -> bytes:
        try:
            descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=tenant_fd)
        except OSError as exc:
            if isinstance(exc, FileNotFoundError):
                raise
            raise ObjectAccessError("Object is not a safe regular file") from exc
        with os.fdopen(descriptor, "rb") as stream:
            details = os.fstat(stream.fileno())
            if not stat.S_ISREG(details.st_mode):
                raise ObjectAccessError("Object is not a regular file")
            if details.st_size > self.max_bytes:
                raise ObjectIntegrityError("Stored object exceeds size limit")
            return stream.read(self.max_bytes + 1)

    async def put(self, tenant_id: str, payload: bytes) -> ObjectRef:
        key, digest = self._payload(tenant_id, payload)
        await self._run(self._put_sync, tenant_id, digest, payload)
        return ObjectRef(
            key=key, version_id=f"local-sha256:{digest}", sha256=digest, size_bytes=len(payload)
        )

    def _put_sync(self, tenant_id: str, digest: str, payload: bytes) -> None:
        directory = self._tenant_fd(tenant_id, create=True)
        temporary = f".tmp-{uuid4().hex}"
        filename = f"{digest}.json"
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode=0o600,
                dir_fd=directory,
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    # link is atomic and never overwrites an existing destination.
                    os.link(
                        temporary,
                        filename,
                        src_dir_fd=directory,
                        dst_dir_fd=directory,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    _verify(self._read(directory, filename), digest, len(payload))
            finally:
                os.unlink(temporary, dir_fd=directory)
                os.fsync(directory)
        finally:
            os.close(directory)

    async def get(self, tenant_id: str, ref: ObjectRef) -> bytes:
        _check_ref(tenant_id, ref, self.max_bytes)
        if ref.version_id != f"local-sha256:{ref.sha256}":
            raise ObjectAccessError("Object version is not a local immutable reference")
        return await self._run(self._get_sync, tenant_id, ref)

    def _get_sync(self, tenant_id: str, ref: ObjectRef) -> bytes:
        directory = self._tenant_fd(tenant_id)
        try:
            return _verify(self._read(directory, f"{ref.sha256}.json"), ref.sha256, ref.size_bytes)
        finally:
            os.close(directory)

    def _close_sync(self) -> None:
        os.close(self._root_fd)


class S3ObjectStore(_ThreadedStore):
    """Versioned S3 adapter; async facade around bounded boto3 blocking IO.

    Conditional writes prevent this adapter from overwriting a live key. Bucket
    policy, versioning, deletion restrictions, encryption and retention are still
    deployment responsibilities. A failed/uncertain upload can leave an orphan
    object; callers reconcile the content-addressed key instead of blind rewrite.
    """

    def __init__(
        self,
        bucket: str,
        *,
        client=None,
        max_bytes: int = 5 * 1024 * 1024,
        concurrency: int = 4,
        region_name: str | None = None,
    ):
        super().__init__(max_bytes, concurrency)
        if not bucket or "/" in bucket:
            raise ValueError("Expected an S3 bucket name")
        self.bucket = bucket
        self._owns_client = client is None
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                region_name=region_name,
                config=Config(
                    connect_timeout=3,
                    read_timeout=10,
                    retries={"mode": "standard", "total_max_attempts": 2},
                ),
            )
        self.client = client

    @staticmethod
    def _version(response: dict) -> str:
        version = response.get("VersionId")
        if not version or version == "null":
            raise ObjectIntegrityError("S3 bucket versioning is required for artifact references")
        return version

    def _read_response(self, response: dict, digest: str, size: int) -> bytes:
        body = response["Body"]
        try:
            if response.get("ContentLength", size) > self.max_bytes:
                raise ObjectIntegrityError("S3 object exceeds size limit")
            payload = body.read(self.max_bytes + 1)
            return _verify(payload, digest, size)
        finally:
            body.close()

    async def put(self, tenant_id: str, payload: bytes) -> ObjectRef:
        key, digest = self._payload(tenant_id, payload)
        version = await self._run(self._put_sync, key, digest, payload)
        return ObjectRef(key=key, version_id=version, sha256=digest, size_bytes=len(payload))

    def _put_sync(self, key: str, digest: str, payload: bytes) -> str:
        from botocore.exceptions import ClientError

        try:
            response = self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=payload,
                ContentType="application/json",
                IfNoneMatch="*",
                ChecksumSHA256=base64.b64encode(hashlib.sha256(payload).digest()).decode(),
                Metadata={"sha256": digest},
            )
            return self._version(response)
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                # 409 conflicts/transient failures belong to the bounded caller/SDK retry policy.
                raise
            # A repeated conditional put can return the existing immutable artifact,
            # but only after reading its bytes and capturing its actual version ID.
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            version = response.get("VersionId")
            self._read_response(response, digest, len(payload))
            return self._version({"VersionId": version})

    async def get(self, tenant_id: str, ref: ObjectRef) -> bytes:
        _check_ref(tenant_id, ref, self.max_bytes)
        if (
            not ref.version_id
            or ref.version_id == "null"
            or ref.version_id.startswith("local-sha256:")
        ):
            raise ObjectAccessError("Expected a versioned S3 reference")
        return await self._run(self._get_sync, ref)

    def _get_sync(self, ref: ObjectRef) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=ref.key, VersionId=ref.version_id)
        response_version = response.get("VersionId")
        payload = self._read_response(response, ref.sha256, ref.size_bytes)
        if response_version != ref.version_id:
            raise ObjectIntegrityError("S3 response version does not match the reference")
        return payload

    def _close_sync(self) -> None:
        if self._owns_client:
            self.client.close()
