import httpx
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from openai import APIStatusError
from pinecone.exceptions import PineconeApiException
from psycopg import OperationalError as PgOperationalError
from sqlalchemy.exc import OperationalError

from incidentops.durable.errors import retryable_dependency_error
from incidentops.durable.repository import Conflict


def response(status):
    return httpx.Response(status, request=httpx.Request("POST", "https://provider.invalid/test"))


@pytest.mark.parametrize(
    "status,expected",
    [(400, False), (401, False), (403, False), (408, True), (429, True), (500, True), (503, True)],
)
def test_vendor_status_classification(status, expected):
    for error in [
        APIStatusError("synthetic", response=response(status), body=None),
        PineconeApiException("synthetic", status_code=status),
        httpx.HTTPStatusError(
            "synthetic", request=response(status).request, response=response(status)
        ),
    ]:
        assert retryable_dependency_error(error) is expected


@pytest.mark.parametrize(
    "code,status,expected",
    [
        ("AccessDenied", 403, False),
        ("InvalidArgument", 400, False),
        ("NoSuchKey", 404, False),
        ("SlowDown", 503, True),
        ("RequestTimeout", 400, True),
        ("InternalError", 500, True),
    ],
)
def test_s3_classifies_safe_codes_without_examining_raw_messages(code, status, expected):
    error = ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, "GetObject"
    )
    assert retryable_dependency_error(error) is expected


@pytest.mark.parametrize(
    "state,expected",
    [
        ("28P01", False),
        ("42501", False),
        ("42P01", False),
        ("08006", True),
        ("40001", True),
        ("40P01", True),
        ("57P01", True),
    ],
)
def test_database_sqlstates_separate_auth_schema_and_transient_failures(state, expected):
    class DriverError(Exception):
        sqlstate = state

    assert retryable_dependency_error(OperationalError(None, None, DriverError())) is expected


def test_connections_are_retryable_and_domain_errors_are_terminal():
    for error in [
        TimeoutError(),
        ConnectionError(),
        PgOperationalError(),
        EndpointConnectionError(endpoint_url="https://object.invalid"),
    ]:
        assert retryable_dependency_error(error)
    for error in [
        Conflict("stale revision"),
        PermissionError(),
        ValueError(),
        httpx.UnsupportedProtocol("configuration"),
    ]:
        assert not retryable_dependency_error(error)
