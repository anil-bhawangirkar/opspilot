import asyncio
from uuid import uuid4

import pytest
from celery.exceptions import Retry
from sqlalchemy.exc import OperationalError

from incidentops.durable import broker
from incidentops.durable.config import WorkflowSettings


class RepositoryCapture:
    def __init__(self, events):
        self.events = events
        self.marked = []

    async def pending_events(self, limit=100):
        return self.events[:limit]

    async def mark_event_published(self, event_id):
        self.marked.append(event_id)


@pytest.mark.asyncio
async def test_outbox_marks_only_after_confirmed_publication():
    job_id = str(uuid4())
    repo = RepositoryCapture([{"id": "event", "job_id": job_id, "queue": "ingestion.small"}])

    class Publisher:
        async def publish(self, published_job, queue):
            assert published_job == job_id
            assert queue == "ingestion.small"
            assert repo.marked == []

    assert await broker.dispatch_once(repo, Publisher()) == 1
    assert repo.marked == ["event"]


@pytest.mark.asyncio
async def test_publication_failure_keeps_outbox_pending_and_allows_replay():
    repo = RepositoryCapture([{"id": "event", "job_id": str(uuid4()), "queue": "investigations"}])

    class Publisher:
        async def publish(self, *args):
            raise ConnectionError("broker unavailable")

    with pytest.raises(ConnectionError):
        await broker.dispatch_once(repo, Publisher())
    assert repo.marked == []


def test_celery_configuration_uses_durable_named_queues_and_late_ack():
    app = broker.create_celery_app(WorkflowSettings(_env_file=None))
    assert app.conf.task_serializer == "json"
    assert app.conf.accept_content == ["json"]
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.broker_transport_options["confirm_publish"] is True
    assert {q.name for q in app.conf.task_queues} == {
        "ingestion.small",
        "ingestion.large",
        "investigations",
    }
    assert all(q.durable for q in app.conf.task_queues)
    assert app.tasks[broker.TASK_NAME].max_retries == 3
    app.close()


@pytest.mark.asyncio
async def test_publisher_sends_only_job_identifier_and_explicit_queue():
    app = broker.create_celery_app(WorkflowSettings(_env_file=None))
    captured = []
    app.send_task = lambda *args, **kwargs: captured.append((args, kwargs))
    publisher = broker.CeleryPublisher(app)
    job_id = str(uuid4())
    await publisher.publish(job_id, "investigations")
    await publisher.aclose()
    assert captured[0][0] == (broker.TASK_NAME,)
    assert captured[0][1]["args"] == [job_id]
    assert captured[0][1]["queue"] == "investigations"
    assert captured[0][1]["retry"] is False
    assert "kwargs" not in captured[0][1]
    app.close()


@pytest.mark.asyncio
async def test_invalid_queue_is_rejected_before_broker_access():
    app = broker.create_celery_app(WorkflowSettings(_env_file=None))
    app.send_task = lambda *args, **kwargs: pytest.fail("broker must not be contacted")
    publisher = broker.CeleryPublisher(app)
    with pytest.raises(ValueError, match="queue"):
        await publisher.publish(str(uuid4()), "tenant-selected-secret-queue")
    await publisher.aclose()
    app.close()


def test_worker_closes_runtime_on_success_and_does_not_return_payload(monkeypatch):
    calls = []

    class Runtime:
        async def run_job(self, job_id):
            calls.append(("run", job_id))
            return True

        async def aclose(self):
            calls.append(("closed", None))

    async def factory(settings):
        return Runtime()

    monkeypatch.setattr(broker, "_create_runtime", factory)
    app = broker.create_celery_app(WorkflowSettings(_env_file=None))
    job_id = str(uuid4())
    assert app.tasks[broker.TASK_NAME].run(job_id) is True
    assert calls == [("run", job_id), ("closed", None)]
    app.close()


def test_preclaim_infrastructure_failure_retries_with_sanitized_exception(monkeypatch):
    async def fail(settings):
        raise OperationalError("sensitive-query", {}, ConnectionError("password=secret-value"))

    monkeypatch.setattr(broker, "_create_runtime", fail)
    app = broker.create_celery_app(WorkflowSettings(_env_file=None))
    task = app.tasks[broker.TASK_NAME]
    # Model an actual worker/eager request, not Task.run's default direct-call mode.
    task.push_request(called_directly=False, is_eager=True, retries=0)
    try:
        with pytest.raises(Retry) as error:
            task.run(str(uuid4()))
    finally:
        task.pop_request()
    assert "secret-value" not in str(error.value)
    assert "sensitive-query" not in str(error.value)
    app.close()


def test_celery_infrastructure_retry_is_finite(monkeypatch):
    async def fail(settings):
        raise ConnectionError("secret=do-not-export")

    monkeypatch.setattr(broker, "_create_runtime", fail)
    app = broker.create_celery_app(WorkflowSettings(_env_file=None))
    task = app.tasks[broker.TASK_NAME]
    task.push_request(called_directly=False, is_eager=True, retries=3)
    try:
        with pytest.raises(broker.WorkerInfrastructureError):
            task.run(str(uuid4()))
    finally:
        task.pop_request()
    app.close()


def test_unclaimed_runtime_marker_retries_and_closes_runtime(monkeypatch):
    from incidentops.durable.runtime import UnclaimedInfrastructureError

    closed = []

    class Runtime:
        async def run_job(self, job_id):
            raise UnclaimedInfrastructureError("persistence unavailable before claim")

        async def aclose(self):
            closed.append(True)

    async def factory(settings):
        return Runtime()

    monkeypatch.setattr(broker, "_create_runtime", factory)
    app = broker.create_celery_app(WorkflowSettings(_env_file=None))
    task = app.tasks[broker.TASK_NAME]
    task.push_request(called_directly=False, is_eager=True, retries=0)
    try:
        with pytest.raises(Retry):
            task.run(str(uuid4()))
    finally:
        task.pop_request()
    assert closed == [True]
    app.close()


def test_configuration_error_is_not_retried(monkeypatch):
    async def fail(settings):
        raise ValueError("missing provider configuration")

    monkeypatch.setattr(broker, "_create_runtime", fail)
    app = broker.create_celery_app(WorkflowSettings(_env_file=None))
    with pytest.raises(broker.WorkerExecutionError):
        app.tasks[broker.TASK_NAME].run(str(uuid4()))
    app.close()


def test_permission_error_is_not_retried(monkeypatch):
    async def fail(settings):
        raise PermissionError("not authorized to read local credentials")

    monkeypatch.setattr(broker, "_create_runtime", fail)
    app = broker.create_celery_app(WorkflowSettings(_env_file=None))
    with pytest.raises(broker.WorkerExecutionError):
        app.tasks[broker.TASK_NAME].run(str(uuid4()))
    app.close()


def test_postclaim_failure_is_not_retried_and_runtime_closes(monkeypatch):
    closed = []

    class Runtime:
        async def run_job(self, job_id):
            raise OperationalError("sensitive-query", {}, ConnectionError("password=secret-value"))

        async def aclose(self):
            closed.append(True)

    async def factory(settings):
        return Runtime()

    monkeypatch.setattr(broker, "_create_runtime", factory)
    app = broker.create_celery_app(WorkflowSettings(_env_file=None))
    with pytest.raises(broker.WorkerExecutionError) as error:
        app.tasks[broker.TASK_NAME].run(str(uuid4()))
    assert "secret-value" not in str(error.value)
    assert closed == [True]
    app.close()


@pytest.mark.asyncio
async def test_publisher_bulkhead_survives_await_cancellation():
    import threading

    entered = threading.Event()
    finish = threading.Event()
    app = broker.create_celery_app(WorkflowSettings(_env_file=None))
    calls = []

    def send(*args, **kwargs):
        calls.append(kwargs["args"])
        entered.set()
        assert finish.wait(5)

    app.send_task = send
    publisher = broker.CeleryPublisher(app, concurrency=1)
    first = asyncio.create_task(publisher.publish(str(uuid4()), "ingestion.small"))
    assert await asyncio.to_thread(entered.wait, 2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(publisher.publish(str(uuid4()), "ingestion.small"))
    await asyncio.sleep(0.02)
    assert len(calls) == 1
    finish.set()
    await second
    await publisher.aclose()
    assert len(calls) == 2
    app.close()
