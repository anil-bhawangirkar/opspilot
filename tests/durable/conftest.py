import pytest

from incidentops.durable.config import WorkflowSettings
from incidentops.durable.runtime import create_runtime


@pytest.fixture
async def runtime(tmp_path):
    cfg = WorkflowSettings(
        _env_file=None,
        provider="fake",
        tracing=False,
        workflow_database_url=f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}",
        object_root=tmp_path / "objects",
        embedding_batch_size=1,
    )
    result = await create_runtime(cfg)
    await result.repo.initialize_for_tests()
    yield result
    await result.aclose()
