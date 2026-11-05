import asyncio
import hashlib

import pytest

from incidentops.models import Chunk
from incidentops.retrieval.contracts import VectorHit
from incidentops.retrieval.hybrid import HybridRetriever, RetrievalPolicy, reciprocal_rank_fusion


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


class Hits:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    async def search(self, tenant_id, vector, generation_ids, limit):
        self.calls.append((tenant_id, generation_ids, limit))
        return self.hits[:limit]


def test_rrf_rewards_agreement_without_comparing_unrelated_raw_scores():
    assert reciprocal_rank_fusion([["a", "b"], ["b", "c"]])[0][0] == "b"


def test_duplicate_ids_in_a_ranked_list_cannot_inflate_rrf():
    normal = reciprocal_rank_fusion([["a", "b"], ["b"]])
    assert reciprocal_rank_fusion([["a", "a", "b"], ["b"]]) == normal


def test_hybrid_recovers_exact_error_code_from_lexical_candidates():
    docs = [
        chunk("semantic", "A generic image publication failure."),
        chunk("exact", "PUB-403 means missing registry permission."),
    ]
    index = Hits([VectorHit("semantic", 0.99)])
    result = asyncio.run(
        HybridRetriever(index).retrieve(
            "acme", "PUB-403 registry permission", [1.0, 0.0], ["gen-1"], docs
        )
    )
    assert "exact" in [e.chunk.chunk_id for e in result.evidence]
    assert index.calls == [("acme", ["gen-1"], 40)]


def test_unknown_remote_hits_are_never_used_as_evidence():
    docs = [chunk("allowed", "PUB-403 missing registry permission.")]
    index = Hits([VectorHit("other-tenant", 1.0), VectorHit("allowed", 0.5)])
    result = asyncio.run(
        HybridRetriever(index).retrieve("acme", "PUB-403", [1.0, 0.0], ["g"], docs)
    )
    assert [e.chunk.chunk_id for e in result.evidence] == ["allowed"]


def test_duplicate_content_consumes_one_slot_and_preserves_all_source_ids():
    docs = [
        chunk("one", "PUB-403 registry permission."),
        chunk("two", "PUB-403 registry permission."),
    ]
    result = asyncio.run(
        HybridRetriever(Hits([VectorHit("two", 0.9), VectorHit("one", 0.8)])).retrieve(
            "acme", "PUB-403", [1.0, 0.0], ["g"], docs
        )
    )
    assert len(result.evidence) == 1
    assert sorted(next(iter(result.duplicate_sources.values()))) == ["one", "two"]


def test_authorized_corpus_cannot_contain_another_tenant():
    with pytest.raises(ValueError, match="tenant"):
        asyncio.run(
            HybridRetriever(Hits([])).retrieve(
                "acme", "PUB-403", [1.0, 0.0], ["g"], [chunk("bravo", "PUB-403", "bravo")]
            )
        )


def test_large_lexical_corpus_is_rejected_instead_of_silently_truncated():
    with pytest.raises(ValueError, match="corpus"):
        asyncio.run(
            HybridRetriever(Hits([]), RetrievalPolicy(max_corpus_chunks=1)).retrieve(
                "acme", "PUB-403", [1.0, 0.0], ["g"], [chunk("one", "a"), chunk("two", "b")]
            )
        )


def test_separate_top_k_budgets_bound_reranking_and_final_evidence():
    class ReverseReranker:
        async def rerank(self, question, candidates):
            assert len(candidates) == 3
            return list(reversed(candidates))

    docs = [chunk(f"doc-{i}", f"PUB-403 registry error detail {i}") for i in range(8)]
    policy = RetrievalPolicy(dense_k=6, lexical_k=5, rerank_k=3, final_k=2)
    result = asyncio.run(
        HybridRetriever(
            Hits([VectorHit(d.chunk_id, 1.0 - i / 10) for i, d in enumerate(docs)]),
            policy,
            ReverseReranker(),
        ).retrieve("acme", "PUB-403", [1.0, 0.0], ["g"], docs)
    )
    assert len(result.evidence) == 2
    assert len(result.fused_ids) <= 3


def test_reranker_cannot_substitute_a_different_source():
    class MaliciousReranker:
        async def rerank(self, question, candidates):
            return [candidates[0].model_copy(update={"chunk": chunk("forged", "Invented fact")})]

    with pytest.raises(ValueError, match="reranker"):
        asyncio.run(
            HybridRetriever(Hits([VectorHit("one", 1.0)]), reranker=MaliciousReranker()).retrieve(
                "acme", "PUB-403", [1.0, 0.0], ["g"], [chunk("one", "PUB-403")]
            )
        )


@pytest.mark.parametrize(
    "kwargs", [{"final_k": 0}, {"rrf_constant": 0}, {"dense_k": 1, "rerank_k": 1, "final_k": 2}]
)
def test_invalid_candidate_budgets_fail_early(kwargs):
    with pytest.raises(ValueError):
        RetrievalPolicy(**kwargs)
