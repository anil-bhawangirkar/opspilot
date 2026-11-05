import os

import pytest

from incidentops.durable import runtime as module
from incidentops.durable.config import WorkflowSettings


def settings_for(tmp_path, **updates):
    return WorkflowSettings(
        _env_file=None,
        provider="fake",
        tracing=False,
        workflow_database_url=f"sqlite+aiosqlite:///{tmp_path / 'state.db'}",
        object_root=tmp_path / "objects",
        **updates,
    )


@pytest.mark.asyncio
async def test_final_runtime_constructor_failure_closes_actual_earlier_adapters(
    tmp_path, monkeypatch
):
    created = {}
    repository_class = module.Repository
    store_class = module.LocalObjectStore
    index_class = module.LocalVectorIndex

    class Repository(repository_class):
        async def aclose(self):
            created["repository_closed"] = True
            await super().aclose()

    class Store(store_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created["store"] = self
            created["root_fd"] = self._root_fd

    class Index(index_class):
        async def aclose(self):
            created["index_closed"] = True
            await super().aclose()

    def broken_runtime(*args, **kwargs):
        raise ValueError("injected final constructor failure")

    monkeypatch.setattr(module, "Repository", Repository)
    monkeypatch.setattr(module, "LocalObjectStore", Store)
    monkeypatch.setattr(module, "LocalVectorIndex", Index)
    monkeypatch.setattr(module, "Runtime", broken_runtime)
    with pytest.raises(ValueError, match="injected final"):
        await module.create_runtime(settings_for(tmp_path))
    assert created["repository_closed"] is True
    assert created["index_closed"] is True
    assert created["store"]._closed is True
    with pytest.raises(OSError):
        os.fstat(created["root_fd"])


@pytest.mark.asyncio
async def test_embedding_factory_failure_closes_owned_actual_http_clients(tmp_path, monkeypatch):
    created = {}
    repository_class = module.Repository
    http_client_class = module.httpx.Client
    async_client_class = module.httpx.AsyncClient

    class Repository(repository_class):
        async def aclose(self):
            created["repository_closed"] = True
            await super().aclose()

    def client(*args, **kwargs):
        created["sync"] = http_client_class(*args, **kwargs)
        return created["sync"]

    def async_client(*args, **kwargs):
        created["async"] = async_client_class(*args, **kwargs)
        return created["async"]

    def failed_embeddings(*args, **kwargs):
        raise ValueError("injected embedding configuration failure")

    config = settings_for(tmp_path).model_copy(update={"provider": "openai"})
    monkeypatch.setattr(module, "Repository", Repository)
    monkeypatch.setattr(module.httpx, "Client", client)
    monkeypatch.setattr(module.httpx, "AsyncClient", async_client)
    monkeypatch.setattr(module, "make_embeddings", failed_embeddings)
    with pytest.raises(ValueError, match="injected embedding"):
        await module.create_runtime(config)
    assert created["repository_closed"] is True
    assert created["sync"].is_closed
    assert created["async"].is_closed


@pytest.mark.asyncio
async def test_successful_construction_transfers_open_resources_to_runtime(tmp_path):
    runtime = await module.create_runtime(settings_for(tmp_path))
    try:
        assert not runtime.objects._closed
        os.fstat(runtime.objects._root_fd)
    finally:
        await runtime.aclose()
    assert runtime.objects._closed
