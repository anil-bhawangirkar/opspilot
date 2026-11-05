"""Conservative retry policy; never infer retryability from raw error messages."""

import httpx
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from openai import APIConnectionError, APIStatusError
from pinecone.exceptions import PineconeApiException
from psycopg import OperationalError as PgOperationalError
from sqlalchemy.exc import OperationalError


def _retryable_status(status):
    return isinstance(status, int) and (status in {408, 429} or 500 <= status <= 599)


def retryable_dependency_error(error: Exception) -> bool:
    if isinstance(error, (OperationalError, PgOperationalError)):
        original = getattr(error, "orig", error)
        state = getattr(original, "sqlstate", None)
        if state:
            return state.startswith(("08", "40", "53")) or state in {
                "55P03",
                "57P01",
                "57P02",
                "57P03",
            }
        return True  # Connection failures and SQLite busy errors have no SQLSTATE.
    if isinstance(error, (APIStatusError, PineconeApiException)):
        return _retryable_status(error.status_code)
    if isinstance(error, httpx.HTTPStatusError):
        return _retryable_status(error.response.status_code)
    if isinstance(error, ClientError):
        details = error.response
        return _retryable_status(details.get("ResponseMetadata", {}).get("HTTPStatusCode")) or (
            details.get("Error", {}).get("Code")
            in {
                "SlowDown",
                "Throttling",
                "ThrottlingException",
                "RequestTimeout",
                "RequestTimeoutException",
                "ServiceUnavailable",
                "InternalError",
            }
        )
    if isinstance(error, (httpx.UnsupportedProtocol, httpx.LocalProtocolError)):
        return False
    return isinstance(
        error,
        (
            TimeoutError,
            ConnectionError,
            httpx.TransportError,
            APIConnectionError,
            EndpointConnectionError,
            ConnectTimeoutError,
            ReadTimeoutError,
            ConnectionClosedError,
        ),
    )
