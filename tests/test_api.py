from fastapi.testclient import TestClient

from incidentops.api import create_app


def test_api_requires_authentication(settings, service):
    with TestClient(create_app(settings, service=service)) as client:
        assert client.post("/v1/ask", json={"question": "PUB-403"}).status_code == 401
        assert client.get("/healthz").json() == {"status": "ok", "increment": 1}


def test_client_cannot_select_tenant(settings, service):
    with TestClient(create_app(settings, service=service)) as client:
        response = client.post(
            "/v1/ask",
            headers={"Authorization": "Bearer acme-demo-token"},
            json={"question": "PUB-403", "tenant_id": "bravo"},
        )
        assert response.status_code == 422


def test_citation_is_readable_only_with_same_tenant_identity(settings, service):
    with TestClient(create_app(settings, service=service)) as client:
        auth = {"Authorization": "Bearer acme-demo-token"}
        response = client.post("/v1/ask", headers=auth, json={"question": "Explain PUB-403"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "answered"
        url = body["claims"][0]["citation"]["source_url"]
        assert client.get(url, headers=auth).status_code == 200
        assert (
            client.get(url, headers={"Authorization": "Bearer bravo-demo-token"}).status_code == 404
        )


def test_unknown_bearer_token_is_rejected(settings, service):
    with TestClient(create_app(settings, service=service)) as client:
        response = client.post(
            "/v1/ask",
            headers={"Authorization": "Bearer invalid-token"},
            json={"question": "PUB-403"},
        )
        assert response.status_code == 401


def test_whitespace_only_question_rejected(settings, service):
    with TestClient(create_app(settings, service=service)) as client:
        response = client.post(
            "/v1/ask", headers={"Authorization": "Bearer acme-demo-token"}, json={"question": "   "}
        )
        assert response.status_code == 422
