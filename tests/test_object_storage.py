import asyncio
import base64
import hashlib
import io
import multiprocessing
import threading
from concurrent.futures import ProcessPoolExecutor

import pytest
from pydantic import ValidationError

from incidentops.object_storage import (
    LocalObjectStore,
    ObjectAccessError,
    ObjectIntegrityError,
    ObjectRef,
    S3ObjectStore,
)


def run(coro):
    return asyncio.run(coro)


def _process_put(root, payload):
    async def operation():
        store = LocalObjectStore(root)
        try:
            return (await store.put("acme", payload)).model_dump()
        finally:
            await store.aclose()

    return run(operation())


def test_local_duplicate_put_and_roundtrip(tmp_path):
    async def operation():
        store = LocalObjectStore(tmp_path)
        payload = b'{"text":"redacted synthetic document"}'
        first = await store.put("acme", payload)
        second = await store.put("acme", payload)
        assert first == second
        assert first.sha256 == hashlib.sha256(payload).hexdigest()
        assert await store.get("acme", first) == payload
        assert not list((tmp_path / "acme").glob(".tmp-*"))
        await store.aclose()

    run(operation())


def test_processes_racing_same_object_publish_one_immutable_value(tmp_path):
    payload = b'{"text":"shared immutable artifact"}'
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=2, mp_context=context) as pool:
        futures = [pool.submit(_process_put, str(tmp_path), payload) for _ in range(4)]
        refs = [future.result(timeout=20) for future in futures]
    assert all(ref == refs[0] for ref in refs)
    assert len(list((tmp_path / "acme").iterdir())) == 1


def test_local_tampering_detected_on_get_and_duplicate_put(tmp_path):
    async def operation():
        store = LocalObjectStore(tmp_path)
        ref = await store.put("acme", b"expected")
        (tmp_path / ref.key).write_bytes(b"tampered")
        with pytest.raises(ObjectIntegrityError):
            await store.get("acme", ref)
        with pytest.raises(ObjectIntegrityError):
            await store.put("acme", b"expected")
        await store.aclose()

    run(operation())


def test_tenant_scope_and_reference_shape_checked_before_access(tmp_path):
    async def operation():
        store = LocalObjectStore(tmp_path)
        ref = await store.put("acme", b"payload")
        with pytest.raises(ObjectAccessError):
            await store.get("bravo", ref)
        with pytest.raises(ObjectAccessError):
            await store.put("../escape", b"payload")
        forged = ref.model_copy(update={"key": "../outside.json"})
        with pytest.raises(ObjectAccessError):
            await store.get("acme", forged)
        with pytest.raises(ObjectAccessError):
            await store.get("acme", ref.model_copy(update={"version_id": "another"}))
        await store.aclose()

    run(operation())


def test_symlink_tenant_and_object_are_rejected(tmp_path):
    async def operation():
        root = tmp_path / "root"
        outside = tmp_path / "outside"
        outside.mkdir()
        store = LocalObjectStore(root)
        (root / "acme").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ObjectAccessError):
            await store.put("acme", b"payload")
        assert list(outside.iterdir()) == []
        (root / "acme").unlink()
        ref = await store.put("acme", b"payload")
        (root / ref.key).unlink()
        secret = outside / "secret"
        secret.write_bytes(b"payload")
        (root / ref.key).symlink_to(secret)
        with pytest.raises(ObjectAccessError):
            await store.get("acme", ref)
        await store.aclose()

    run(operation())


def test_size_limit_and_closed_store(tmp_path):
    async def operation():
        store = LocalObjectStore(tmp_path, max_bytes=4)
        with pytest.raises(ValueError, match="size"):
            await store.put("acme", b"12345")
        ref = await store.put("acme", b"1234")
        (tmp_path / ref.key).write_bytes(b"123456")
        with pytest.raises(ObjectIntegrityError):
            await store.get("acme", ref)
        await store.aclose()
        with pytest.raises(RuntimeError, match="closed"):
            await store.put("acme", b"1234")

    run(operation())


def test_object_ref_is_strict_and_immutable():
    with pytest.raises(ValidationError):
        ObjectRef(key="acme/whatever.json", version_id="v1", sha256="bad", size_bytes="5")


def test_cancelled_thread_retains_bulkhead_permit_until_io_finishes(tmp_path):
    async def operation():
        started = threading.Event()
        release = threading.Event()

        class BlockingStore(LocalObjectStore):
            calls = 0

            def _put_sync(self, *args):
                self.calls += 1
                started.set()
                assert release.wait(timeout=5)
                return super()._put_sync(*args)

        store = BlockingStore(tmp_path, concurrency=1)
        first = asyncio.create_task(store.put("acme", b"first"))
        assert await asyncio.to_thread(started.wait, 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(store.put("acme", b"second"))
        await asyncio.sleep(0.01)
        assert store.calls == 1
        release.set()
        ref = await second
        assert await store.get("acme", ref) == b"second"
        await store.aclose()

    run(operation())


@pytest.fixture
def s3_client():
    boto3 = pytest.importorskip("boto3")
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
        endpoint_url="https://s3.example.test",
    )
    yield client
    client.close()


def put_parameters(payload):
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "Bucket": "synthetic-artifacts",
        "Key": f"acme/{digest}.json",
        "Body": payload,
        "ContentType": "application/json",
        "IfNoneMatch": "*",
        "ChecksumSHA256": base64.b64encode(hashlib.sha256(payload).digest()).decode(),
        "Metadata": {"sha256": digest},
    }


def response_body(payload, version="version-1"):
    from botocore.response import StreamingBody

    return {
        "Body": StreamingBody(io.BytesIO(payload), len(payload)),
        "ContentLength": len(payload),
        "VersionId": version,
    }


def test_s3_conditional_put_and_exact_version_get(s3_client):
    from botocore.stub import Stubber

    async def operation():
        payload = b'{"text":"synthetic"}'
        params = put_parameters(payload)
        with Stubber(s3_client) as stub:
            stub.add_response("put_object", {"VersionId": "version-1"}, params)
            stub.add_response(
                "get_object",
                response_body(payload),
                {
                    "Bucket": params["Bucket"],
                    "Key": params["Key"],
                    "VersionId": "version-1",
                },
            )
            store = S3ObjectStore("synthetic-artifacts", client=s3_client)
            ref = await store.put("acme", payload)
            assert ref.version_id == "version-1"
            assert await store.get("acme", ref) == payload
            stub.assert_no_pending_responses()
            await store.aclose()

    run(operation())


def test_s3_duplicate_put_reads_and_verifies_current_version(s3_client):
    from botocore.stub import Stubber

    async def operation():
        payload = b"same payload"
        params = put_parameters(payload)
        with Stubber(s3_client) as stub:
            stub.add_client_error(
                "put_object",
                service_error_code="PreconditionFailed",
                http_status_code=412,
                expected_params=params,
            )
            stub.add_response(
                "get_object",
                response_body(payload, "existing-version"),
                {"Bucket": params["Bucket"], "Key": params["Key"]},
            )
            store = S3ObjectStore("synthetic-artifacts", client=s3_client)
            ref = await store.put("acme", payload)
            assert ref.version_id == "existing-version"
            stub.assert_no_pending_responses()
            await store.aclose()

    run(operation())


def test_s3_unversioned_bucket_fails_explicitly(s3_client):
    from botocore.stub import Stubber

    async def operation():
        payload = b"payload"
        with Stubber(s3_client) as stub:
            stub.add_response("put_object", {}, put_parameters(payload))
            store = S3ObjectStore("synthetic-artifacts", client=s3_client)
            with pytest.raises(ObjectIntegrityError, match="version"):
                await store.put("acme", payload)
            await store.aclose()

    run(operation())


def test_s3_rejects_cross_tenant_ref_without_network(s3_client):
    from botocore.stub import Stubber

    async def operation():
        digest = hashlib.sha256(b"payload").hexdigest()
        ref = ObjectRef(key=f"acme/{digest}.json", version_id="v1", sha256=digest, size_bytes=7)
        with Stubber(s3_client):
            store = S3ObjectStore("synthetic-artifacts", client=s3_client)
            with pytest.raises(ObjectAccessError):
                await store.get("bravo", ref)
            await store.aclose()

    run(operation())


def test_s3_payload_tampering_is_detected(s3_client):
    from botocore.stub import Stubber

    async def operation():
        digest = hashlib.sha256(b"expected").hexdigest()
        ref = ObjectRef(key=f"acme/{digest}.json", version_id="v1", sha256=digest, size_bytes=8)
        with Stubber(s3_client) as stub:
            stub.add_response(
                "get_object",
                response_body(b"tampered", "v1"),
                {
                    "Bucket": "synthetic-artifacts",
                    "Key": ref.key,
                    "VersionId": "v1",
                },
            )
            store = S3ObjectStore("synthetic-artifacts", client=s3_client)
            with pytest.raises(ObjectIntegrityError):
                await store.get("acme", ref)
            await store.aclose()

    run(operation())
