"""Bounded hybrid lab: BM25 over an explicitly authorized corpus plus dense RRF.

The corpus cap is deliberate. This is not a distributed lexical search service;
replace the lexical adapter when the authorized corpus exceeds that bound.
"""

import asyncio
import math
import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from incidentops.models import Chunk, Evidence
from incidentops.retrieval.contracts import AsyncReranker, AsyncVectorIndex


@dataclass(frozen=True)
class RetrievalPolicy:
    dense_k: int = 40
    lexical_k: int = 40
    rerank_k: int = 20
    final_k: int = 5
    rrf_constant: int = 60
    max_corpus_chunks: int = 2000

    def __post_init__(self):
        for value in (self.dense_k, self.lexical_k, self.rerank_k, self.final_k):
            if not 1 <= value <= 1000:
                raise ValueError("Candidate budgets must be between 1 and 1000")
        if self.final_k > self.rerank_k or self.rrf_constant < 1:
            raise ValueError("Require final_k <= rerank_k and positive RRF constant")
        if not 1 <= self.max_corpus_chunks <= 100000:
            raise ValueError("Invalid authorized corpus cap")


@dataclass(frozen=True)
class HybridResult:
    evidence: list[Evidence]
    duplicate_sources: dict[str, list[str]]
    dense_ids: list[str]
    lexical_ids: list[str]
    fused_ids: list[str]


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    constant: int = 60,
) -> list[tuple[str, float]]:
    if constant < 1:
        raise ValueError("RRF constant must be positive")
    scores: dict[str, float] = {}
    for ranking in rankings:
        unique = dict.fromkeys(ranking)
        for rank, identifier in enumerate(unique, start=1):
            scores[identifier] = scores.get(identifier, 0.0) + 1 / (constant + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+(?:[-.]\w+)*", text.lower())


def _lexical_ranking(question: str, chunks: list[Chunk], limit: int) -> list[str]:
    corpus = [_tokens(chunk.text) for chunk in chunks]
    query = _tokens(question)
    if not query or not any(corpus):
        return []
    scores = BM25Okapi(corpus).get_scores(query)
    query_tokens = set(query)
    # A common term can have a zero or negative BM25 score. Match membership is
    # separate from score sign; absent terms must never select unrelated chunks.
    selected = [
        (chunk.chunk_id, float(score))
        for chunk, words, score in zip(chunks, corpus, scores, strict=True)
        if query_tokens.intersection(words)
    ]
    return [
        identifier
        for identifier, _ in sorted(selected, key=lambda item: (-item[1], item[0]))[:limit]
    ]


class HybridRetriever:
    def __init__(
        self,
        index: AsyncVectorIndex,
        policy: RetrievalPolicy | None = None,
        reranker: AsyncReranker | None = None,
    ):
        self.index = index
        self.policy = policy or RetrievalPolicy()
        self.reranker = reranker

    async def retrieve(
        self,
        tenant_id: str,
        question: str,
        vector: list[float],
        generation_ids: list[str],
        authorized_chunks: list[Chunk],
    ) -> HybridResult:
        policy = self.policy
        if len(authorized_chunks) > policy.max_corpus_chunks:
            raise ValueError("Authorized lexical corpus exceeds configured cap")
        if any(chunk.tenant_id != tenant_id for chunk in authorized_chunks):
            raise ValueError("Authorized corpus contains another tenant")
        authorized = {chunk.chunk_id: chunk for chunk in authorized_chunks}
        if len(authorized) != len(authorized_chunks):
            raise ValueError("Authorized corpus contains duplicate chunk IDs")
        if not authorized or not generation_ids:
            return HybridResult([], {}, [], [], [])
        hits, lexical_ids = await asyncio.gather(
            self.index.search(tenant_id, vector, generation_ids, policy.dense_k),
            asyncio.to_thread(_lexical_ranking, question, authorized_chunks, policy.lexical_k),
        )
        dense_ids = list(
            dict.fromkeys(
                hit.chunk_id
                for hit in hits
                if hit.chunk_id in authorized and math.isfinite(hit.score)
            )
        )[: policy.dense_k]
        fused = reciprocal_rank_fusion([dense_ids, lexical_ids], policy.rrf_constant)

        representatives: dict[str, str] = {}
        duplicate_sources: dict[str, list[str]] = {}
        candidates = []
        for identifier, score in fused:
            chunk = authorized[identifier]
            # Authorization precedes dedup; never attach hidden provenance.
            fingerprint = chunk.content_hash
            if fingerprint in representatives:
                duplicate_sources[representatives[fingerprint]].append(identifier)
                continue
            representatives[fingerprint] = identifier
            duplicate_sources[identifier] = [identifier]
            candidates.append(Evidence(chunk=chunk, score=score))
        candidates = candidates[: policy.rerank_k]
        permitted = {candidate.chunk.chunk_id: candidate.chunk for candidate in candidates}
        if self.reranker is not None and candidates:
            reranked = await self.reranker.rerank(question, candidates)
            seen = set()
            for item in reranked:
                identifier = item.chunk.chunk_id
                if (
                    identifier not in permitted
                    or item.chunk != permitted[identifier]
                    or identifier in seen
                    or not math.isfinite(item.score)
                ):
                    raise ValueError("Invalid reranker output")
                seen.add(identifier)
            evidence = reranked[: policy.final_k]
        else:
            evidence = candidates[: policy.final_k]
        retained = {item.chunk.chunk_id for item in evidence}
        return HybridResult(
            evidence=evidence,
            duplicate_sources={
                key: sorted(values) for key, values in duplicate_sources.items() if key in retained
            },
            dense_ids=dense_ids,
            lexical_ids=lexical_ids,
            fused_ids=[item.chunk.chunk_id for item in candidates],
        )

    async def rank(self, *args, **kwargs) -> list[Evidence]:
        return (await self.retrieve(*args, **kwargs)).evidence
