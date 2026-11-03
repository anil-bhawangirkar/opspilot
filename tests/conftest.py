from pathlib import Path

import pytest

from incidentops.config import Settings
from incidentops.factory import build_service
from incidentops.ingestion import ingest
from incidentops.models import Principal


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        provider="fake",
        tracing=False,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        corpus_dir=Path("data/corpus"),
    )


@pytest.fixture
def service(settings):
    result = build_service(settings)
    ingest(settings.corpus_dir, result.store, result.embeddings, settings.chunk_tokens)
    return result


@pytest.fixture
def acme():
    return Principal(tenant_id="acme", principal_id="alice", groups=("operators",))


@pytest.fixture
def bravo():
    return Principal(tenant_id="bravo", principal_id="bob", groups=("operators",))
