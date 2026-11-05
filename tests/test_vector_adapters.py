import asyncio
import hashlib
import os
from uuid import uuid4

import pytest

from incidentops.models import Chunk
from incidentops.retrieval.indexes import MemoryVectorIndex, PgVectorIndex, PineconeVectorIndex


def chunk(identifier, text, tenant="acme"):
    return Chunk(
        document_id=identifier,
        tenant_id=tenant,
        version=1,
        title=identifier,
        service="image-publisher",
        region="west",
        release="2.7",
        access_group="operators",
        status="active",
        effective_from="2026-01-01",
        effective_to=None,
        text=text,
        chunk_id=identifier,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        document_hash="document-hash",
        start_offset=0,
        end_offset=len(text),
    )


def test_memory_adapter_repeated_upsert_is_idempotent_and_tenant_generation_scoped():
    async def exercise():
        index = MemoryVectorIndex(2, "fake-v1")
        acme, bravo = chunk("one", "Acme text"), chunk("two", "Bravo text", "bravo")
        await index.upsert("acme", "old", [acme], [[1.0, 0.0]])
        await index.upsert("acme", "new", [acme], [[1.0, 0.0]])
        await index.upsert("acme", "new", [acme], [[1.0, 0.0]])
        await index.upsert("bravo", "new", [bravo], [[1.0, 0.0]])
        hits = await index.search("acme", [1.0, 0.0], ["new"], 5)
        assert [h.chunk_id for h in hits] == ["one"]
        await index.delete_generation("acme", "new")
        assert await index.search("acme", [1.0, 0.0], ["new"], 5) == []
        assert await index.search("acme", [1.0, 0.0], ["old"], 5)
        assert await index.search("bravo", [1.0, 0.0], ["new"], 5)

    asyncio.run(exercise())


def test_vector_validation_prevents_cross_tenant_or_invalid_vector_writes():
    async def exercise():
        index = MemoryVectorIndex(2, "fake-v1")
        with pytest.raises(ValueError, match="tenant"):
            await index.upsert("acme", "g", [chunk("b", "Bravo", "bravo")], [[1.0, 0.0]])
        for vector in [[0.0, 0.0], [float("nan"), 1.0], [1.0]]:
            with pytest.raises(ValueError):
                await index.upsert("acme", "g", [chunk("a", "Acme")], [vector])
        assert await index.search("acme", [1.0, 0.0], [], 5) == []

    asyncio.run(exercise())


class CapturingPineconeIndex:
    def __init__(self):
        self.calls = []

    async def upsert(self, **kwargs):
        self.calls.append(("upsert", kwargs))

    async def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return {
            "matches": [
                {
                    "id": "external",
                    "score": 0.8,
                    "metadata": {
                        "chunk_id": "one",
                        "generation_id": "g",
                        "embedding_identity": "fake-v1",
                    },
                }
            ]
        }

    async def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))

    async def close(self):
        self.calls.append(("close", {}))


def test_pinecone_contract_uses_namespace_generations_and_deterministic_ids():
    async def exercise():
        client = CapturingPineconeIndex()
        index = PineconeVectorIndex(client, dimensions=2, embedding_identity="fake-v1")
        doc = chunk("one", "PUB-403")
        await index.upsert("acme", "g", [doc], [[1.0, 0.0]])
        await index.upsert("acme", "g", [doc], [[1.0, 0.0]])
        assert client.calls[0][1]["vectors"] == client.calls[1][1]["vectors"]
        assert client.calls[0][1]["namespace"] == "acme"
        hits = await index.search("acme", [1.0, 0.0], ["g"], 10)
        assert hits[0].chunk_id == "one"
        request = client.calls[-1][1]
        assert request["namespace"] == "acme"
        assert request["filter"]["$and"][0] == {"generation_id": {"$in": ["g"]}}
        await index.delete_generation("acme", "g")
        assert client.calls[-1][1]["namespace"] == "acme"
        assert client.calls[-1][1]["filter"]["$and"][0] == {"generation_id": {"$eq": "g"}}
        await index.aclose()

    asyncio.run(exercise())


def test_pinecone_empty_generation_set_makes_no_remote_call():
    async def exercise():
        client = CapturingPineconeIndex()
        index = PineconeVectorIndex(client, dimensions=2, embedding_identity="fake-v1")
        assert await index.search("acme", [1.0, 0.0], [], 10) == []
        assert client.calls == []

    asyncio.run(exercise())


def test_pinecone_ignores_stale_or_mismatched_remote_metadata():
    class StaleClient(CapturingPineconeIndex):
        async def query(self, **kwargs):
            return {
                "matches": [
                    {
                        "id": "old",
                        "score": 1.0,
                        "metadata": {
                            "chunk_id": "old",
                            "generation_id": "old",
                            "embedding_identity": "fake-v1",
                        },
                    },
                    {
                        "id": "wrong",
                        "score": 1.0,
                        "metadata": {
                            "chunk_id": "wrong",
                            "generation_id": "g",
                            "embedding_identity": "other",
                        },
                    },
                ]
            }

    async def exercise():
        index = PineconeVectorIndex(StaleClient(), dimensions=2, embedding_identity="fake-v1")
        assert await index.search("acme", [1.0, 0.0], ["g"], 10) == []

    asyncio.run(exercise())


def test_pinecone_sdk_can_construct_and_close_without_network_calls():
    async def exercise():
        index = PineconeVectorIndex.from_host(
            api_key="synthetic-not-a-real-key",
            host="https://synthetic-index.example.test",
            dimensions=2,
            embedding_identity="fake-v1",
        )
        await index.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"m": 1},
        {"ef_construction": 1},
        {"ef_search": 0},
        {"m": 100, "ef_construction": 64},
        {"m": 16.5},
    ],
)
def test_pgvector_hnsw_parameters_are_bounded_before_sql(kwargs):
    with pytest.raises(ValueError):
        PgVectorIndex("postgresql://unused", dimensions=2, embedding_identity="fake-v1", **kwargs)


@pytest.mark.parametrize("operation", ["upsert", "search", "delete", "validate"])
def test_pgvector_runtime_rejects_same_dimension_wrong_model_without_ddl(monkeypatch, operation):
    import psycopg

    statements = []

    class Cursor:
        async def fetchone(self):
            return ("actual-model", 2)

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, sql, params=()):
            statements.append(sql)
            return Cursor()

    async def connect(*args, **kwargs):
        return Connection()

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)

    async def exercise():
        # Intentionally no initialize(): runtime role need not have DDL privileges.
        index = PgVectorIndex("postgresql://unused", dimensions=2, embedding_identity="wrong-model")
        with pytest.raises(ValueError, match="configuration"):
            if operation == "upsert":
                await index.upsert("acme", "g", [chunk("one", "PUB-403")], [[1.0, 0.0]])
            elif operation == "search":
                await index.search("acme", [1.0, 0.0], ["g"], 5)
            elif operation == "delete":
                await index.delete_generation("acme", "g")
            else:
                await index.validate_configuration()

    asyncio.run(exercise())
    assert statements
    assert all(statement.startswith("SELECT embedding_identity") for statement in statements)


@pytest.mark.postgres
def test_pgvector_real_roundtrip_hnsw_and_exact_reference():
    url = os.getenv("INCIDENTOPS_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires an actual disposable pgvector database")

    async def exercise():
        # Separate vector table from the legacy corpus store, configured once per test DB.
        index = PgVectorIndex(
            url, dimensions=256, embedding_identity="fake-hashed-words-v1:256", hnsw=True
        )
        await index.initialize()
        generation = str(uuid4())
        vector = [1.0] + [0.0] * 255
        try:
            await index.upsert("acme", generation, [chunk("vector-test", "PUB-403")], [vector])
            assert (await index.search("acme", vector, [generation], 5))[
                0
            ].chunk_id == "vector-test"
            assert await index.search("bravo", vector, [generation], 5) == []
            index.exact = True
            assert (await index.search("acme", vector, [generation], 5))[
                0
            ].chunk_id == "vector-test"
            with pytest.raises(ValueError, match="configuration"):
                await PgVectorIndex(url, dimensions=2, embedding_identity="wrong").initialize()
            wrong_model = PgVectorIndex(
                url, dimensions=256, embedding_identity="same-size-wrong-model"
            )
            # A worker/reader opens an existing table without running initialize().
            with pytest.raises(ValueError, match="configuration"):
                await wrong_model.upsert("acme", generation, [chunk("wrong", "PUB-403")], [vector])
            with pytest.raises(ValueError, match="configuration"):
                await wrong_model.search("acme", vector, [generation], 5)
        finally:
            await index.delete_generation("acme", generation)

    asyncio.run(exercise())
