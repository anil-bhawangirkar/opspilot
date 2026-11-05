from langchain_core.embeddings import Embeddings
from langsmith import tracing_context

from incidentops.models import Evidence, Principal, Question
from incidentops.ports import EvidenceIndex
from incidentops.security import redact


class DenseRetriever:
    """Evidence retrieval use case; can be hosted locally or behind a future service API."""

    def __init__(self, index: EvidenceIndex, embeddings: Embeddings, *, top_k: int = 5):
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        self.index, self.embeddings, self.top_k = index, embeddings, top_k

    def retrieve(self, principal: Principal, question: Question) -> list[Evidence]:
        with tracing_context(enabled=False):
            vector = self.embeddings.embed_query(redact(question.question))
        return self.index.search(principal, question, vector, self.top_k)
