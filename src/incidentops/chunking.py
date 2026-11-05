"""LangChain splitting adapter; domain validation and citation identity stay explicit."""

import hashlib
import json
from dataclasses import dataclass

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from incidentops.models import Chunk, SourceDocument
from incidentops.security import redact


def to_langchain_document(document: SourceDocument) -> Document:
    metadata = document.model_dump(mode="json", exclude={"text"})
    metadata["title"] = redact(document.title)
    return Document(page_content=redact(document.text), metadata=metadata)


@dataclass(frozen=True)
class RecursiveDocumentChunker:
    size_chars: int = 350
    overlap_chars: int = 40

    def __post_init__(self):
        if self.size_chars <= 0 or not 0 <= self.overlap_chars < self.size_chars:
            raise ValueError("Require size_chars > 0 and 0 <= overlap_chars < size_chars")

    @property
    def profile(self) -> str:
        # Profile is part of identity: re-splitting must not silently reuse old citation IDs.
        return f"recursive-chars-v1:size={self.size_chars}:overlap={self.overlap_chars}"

    def split(self, document: SourceDocument) -> list[Chunk]:
        source = to_langchain_document(document)
        if not source.page_content.strip():
            raise ValueError("Cannot chunk an empty document")
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.size_chars,
            chunk_overlap=self.overlap_chars,
            length_function=len,
            add_start_index=True,
            strip_whitespace=False,
        )
        document_hash = hashlib.sha256(source.page_content.encode()).hexdigest()
        result = []
        for ordinal, part in enumerate(splitter.split_documents([source])):
            start = part.metadata["start_index"]
            end = start + len(part.page_content)
            if start < 0 or source.page_content[start:end] != part.page_content:
                raise ValueError("Splitter returned invalid source offsets")
            if len(part.page_content) > self.size_chars:
                raise ValueError("Splitter exceeded character budget")
            digest = hashlib.sha256(part.page_content.encode()).hexdigest()
            identity = json.dumps(
                [
                    document.tenant_id,
                    document.document_id,
                    document.version,
                    self.profile,
                    ordinal,
                    digest,
                ],
                separators=(",", ":"),
            )
            result.append(
                Chunk(
                    **source.metadata,
                    text=part.page_content,
                    chunk_id=hashlib.sha256(identity.encode()).hexdigest(),
                    content_hash=digest,
                    document_hash=document_hash,
                    start_offset=start,
                    end_offset=end,
                    splitter_profile=self.profile,
                )
            )
        return result
