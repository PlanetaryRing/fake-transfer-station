import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from fake_transfer_station.app import create_app
from fake_transfer_station.content import claude_id


@pytest.mark.parametrize("headers,query", [
    ({"Authorization": "Bearer test-key"}, ""),
    ({"Authorization": "bEaReR test-key"}, ""),
    ({"Authorization": "test-key"}, ""),
    ({"x-api-key": "test-key"}, ""),
    ({"x-goog-api-key": "test-key"}, ""),
    ({}, "?key=test-key"),
    ({}, "?auth_token=test-key"),
    ({"Authorization": "Bearer wrong", "x-api-key": "test-key"}, ""),
    ({"Authorization": "Bearer test-key", "x-api-key": "wrong"}, ""),
])
def test_cpa_auth_candidates(app, headers, query):
    with TestClient(app) as client:
        assert client.get("/v1/models" + query, headers=headers).status_code == 200


def test_auth_errors_and_public_routes(app):
    with TestClient(app) as client:
        missing = client.get("/v1/models")
        assert missing.status_code == 401
        assert missing.json() == {"error": "Missing API key"}
        wrong = client.post("/v1/messages", headers={"x-api-key": "wrong"}, json={})
        assert wrong.status_code == 401
        assert wrong.json() == {"error": "Invalid API key"}
        assert client.get("/").json() == {"message": "CLI Proxy API Server", "endpoints": [
            "POST /v1/chat/completions", "POST /v1/completions", "GET /v1/models"]}
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.head("/healthz").content == b""
        preflight = client.options("/v1/messages", headers={"Origin": "https://example.test"})
        assert preflight.status_code == 204
        assert preflight.headers["access-control-allow-origin"] == "*"


def test_empty_keys_allow_anonymous(config):
    config.access.api_keys = []
    with TestClient(create_app(config)) as client:
        assert client.get("/v1/models").status_code == 200


def test_model_catalog_dispatch(client):
    ordinary = client.get("/v1/models").json()
    assert ordinary["object"] == "list"
    assert ordinary["data"][0] == {"id": "gpt-test", "object": "model", "created": 1700000000, "owned_by": "openai"}
    # An x-api-key alone must NOT select the Anthropic catalog.
    assert client.get("/v1/models", headers={"x-api-key": "test-key"}).json()["object"] == "list"
    for headers in ({"anthropic-version": "2023-06-01"}, {"User-Agent": "claude-cli/2.0"}):
        catalog = client.get("/v1/models", headers=headers).json()
        assert catalog["has_more"] is False
        assert catalog["first_id"] == claude_id("gpt-test")
        assert catalog["last_id"] == "claude-test"
        detail = client.get("/v1/models/" + catalog["first_id"], headers=headers)
        assert detail.json() == catalog["data"][0]
    codex = client.get("/v1/models?client_version=cpa", headers={"anthropic-version": "2023-06-01"}).json()
    assert codex["models"][0]["slug"] == "gpt-test"
    assert codex["models"][0]["apply_patch_tool_type"] is None
    assert codex["models"][0]["cpa_capabilities"] == {"web_search": False}
    assert client.get("/v1/models/not-found").status_code == 404
    assert client.get("/v1beta/models").json()["models"][0]["name"] == "models/gpt-test"
    assert client.get("/v1beta/models/gpt-test").json()["displayName"] == "A GPT"


def test_cloaked_id_is_callable(client):
    result = client.post("/v1/messages", json={"model": claude_id("gpt-test"), "messages": [], "max_tokens": 10})
    assert result.status_code == 200
    assert result.json()["model"] == claude_id("gpt-test")


def test_websocket_auth_denied_before_upgrade(app):
    with TestClient(app) as client:
        with pytest.raises(WebSocketDenialResponse) as denied:
            with client.websocket_connect("/v1/responses"):
                pass
        assert denied.value.status_code == 401
        assert denied.value.json() == {"error": "Missing API key"}
