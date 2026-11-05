"""At-least-once RabbitMQ dispatch with durable database state as the authority.

Broker messages contain a job UUID only. Publisher confirms establish broker
acceptance, not execution or exactly-once effects. The database lease/reaper
handles claimed-work recovery; Celery retries only explicitly unclaimed IO.
"""

import asyncio
import errno
import logging
import random
from typing import Protocol
from uuid import UUID

from celery import Celery
from kombu import Queue
from sqlalchemy.exc import OperationalError

from incidentops.durable.config import WorkflowSettings

TASK_NAME = "incidentops.execute_job"
QUEUES = ("ingestion.small", "ingestion.large", "investigations")
logger = logging.getLogger(__name__)


class WorkerInfrastructureError(RuntimeError):
    """Sanitized unclaimed failure safe for broker diagnostics."""


class WorkerExecutionError(RuntimeError):
    """Sanitized claimed/configuration failure; durable state owns recovery."""


class _BeforeClaimFailure(RuntimeError):
    pass


class Publisher(Protocol):
    async def publish(self, job_id: str, queue: str) -> None: ...


def _job_id(value: str) -> None:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Invalid job identifier")


async def dispatch_once(repository, publisher: Publisher, *, limit: int = 100) -> int:
    """Dispatch a bounded eligible outbox page; failures leave the event pending.

    Two dispatchers or a crash after send may publish duplicates. No DB/broker
    transaction is implied; the worker's fenced claim deduplicates execution.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("Invalid dispatcher page limit")
    published = 0
    for event in await repository.pending_events(limit=limit):
        queue = event["queue"]
        if queue not in QUEUES:
            raise ValueError("Invalid job queue")
        _job_id(event["job_id"])
        await publisher.publish(event["job_id"], queue)
        await repository.mark_event_published(event["id"])
        published += 1
    return published


async def _create_runtime(settings):
    # Lazy import avoids constructing provider/DB clients while Celery loads config.
    from incidentops.durable.runtime import create_runtime

    return await create_runtime(settings)


async def _execute(settings, job_id):
    from incidentops.durable.runtime import UnclaimedInfrastructureError

    runtime = None
    try:
        try:
            runtime = await _create_runtime(settings)
        except PermissionError:
            raise
        except (OperationalError, ConnectionError, TimeoutError):
            raise _BeforeClaimFailure from None
        except OSError as exc:
            if exc.errno in {
                errno.ECONNABORTED,
                errno.ECONNREFUSED,
                errno.ECONNRESET,
                errno.EHOSTUNREACH,
                errno.ENETDOWN,
                errno.ENETUNREACH,
                errno.ETIMEDOUT,
            }:
                raise _BeforeClaimFailure from None
            raise
        try:
            return await runtime.run_job(job_id)
        except UnclaimedInfrastructureError:
            raise _BeforeClaimFailure from None
    finally:
        if runtime is not None:
            try:
                await runtime.aclose()
            except Exception:
                # Cleanup failure must not repeat already-committed business work,
                # nor expose connection strings or provider payloads in Celery logs.
                logger.warning("Worker runtime cleanup unavailable")


def create_celery_app(settings: WorkflowSettings) -> Celery:
    application = Celery("incidentops", broker=settings.broker_url.get_secret_value())
    application.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_ignore_result=True,
        task_store_errors_even_if_ignored=False,
        task_queues=tuple(Queue(name, durable=True) for name in QUEUES),
        task_default_queue="investigations",
        task_create_missing_queues=False,
        task_default_delivery_mode="persistent",
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        broker_transport_options={"confirm_publish": True},
        broker_connection_retry_on_startup=True,
        broker_connection_max_retries=5,
        broker_connection_timeout=5,
        task_publish_retry=False,
    )

    @application.task(bind=True, name=TASK_NAME, max_retries=3, ignore_result=True, shared=False)
    def execute_job(self, job_id):
        _job_id(job_id)
        try:
            return asyncio.run(_execute(settings, job_id))
        except _BeforeClaimFailure:
            if self.request.retries >= self.max_retries:
                raise WorkerInfrastructureError("Worker infrastructure unavailable") from None
            countdown = random.uniform(0.5, min(30, 2 ** (self.request.retries + 1)))
            retry = self.retry(
                exc=WorkerInfrastructureError("Worker infrastructure unavailable"),
                countdown=countdown,
                throw=False,
            )
            raise retry from None
        except Exception:
            raise WorkerExecutionError(
                "Worker execution unavailable; inspect durable job state"
            ) from None

    return application


class CeleryPublisher:
    """Bound blocking AMQP IO and retain permits until cancelled thread work ends."""

    def __init__(self, application: Celery, *, concurrency: int = 2):
        if concurrency <= 0:
            raise ValueError("Publisher concurrency must be positive")
        self.application = application
        self._permits = asyncio.Semaphore(concurrency)
        self._pending: set[asyncio.Task] = set()
        self._closed = False

    async def publish(self, job_id: str, queue: str) -> None:
        _job_id(job_id)
        if queue not in QUEUES:
            raise ValueError("Invalid job queue")
        if self._closed:
            raise RuntimeError("Publisher is closed")
        await self._permits.acquire()
        if self._closed:
            self._permits.release()
            raise RuntimeError("Publisher is closed")
        task = asyncio.create_task(
            asyncio.to_thread(
                self.application.send_task,
                TASK_NAME,
                args=[job_id],
                queue=queue,
                task_id=job_id,
                serializer="json",
                delivery_mode=2,
                retry=False,
            )
        )
        self._pending.add(task)

        def finished(completed):
            self._pending.discard(completed)
            self._permits.release()
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(finished)
        await asyncio.shield(task)

    async def aclose(self):
        self._closed = True
        if self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)


app = create_celery_app(WorkflowSettings())
