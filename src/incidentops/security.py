"""Small synthetic-data redactor. It is not a general PII detection product."""

import re
from hmac import compare_digest
from typing import Any

from incidentops.models import Principal

_PATTERNS = [
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.I),
    re.compile(r"\b(?:api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,;]+", re.I),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
]


def redact(value: Any) -> Any:
    if isinstance(value, str):
        for pattern in _PATTERNS:
            value = pattern.sub("[REDACTED]", value)
        return value
    if isinstance(value, dict):
        return {redact(str(key)): redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def token_upper_bound(text: str) -> int:
    # Byte-level tokenizers cannot produce more tokens than input UTF-8 bytes.
    # Pessimistic on purpose: offline execution never downloads a tokenizer.
    return len(text.encode("utf-8"))


def authenticate(token: str, acme_token: str, bravo_token: str) -> Principal | None:
    # Trusted application configuration, never model-produced identity.
    if compare_digest(token.encode(), acme_token.encode()):
        return Principal("acme", "acme-demo-operator", ("operators",))
    if compare_digest(token.encode(), bravo_token.encode()):
        return Principal("bravo", "bravo-demo-operator", ("operators",))
    return None
