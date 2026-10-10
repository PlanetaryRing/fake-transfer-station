import hashlib

import pytest
from fastapi.testclient import TestClient

from fake_transfer_station.app import create_app


HTML_SHA256 = "aae0653e65dc81668ded759c8e93d474ce295e0b159a1ca205bf0b6316655df1"


def test_public_page_is_exact_upstream_release(app):
    with TestClient(app) as client:
        response = client.get("/management.html")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert hashlib.sha256(response.content).hexdigest() == HTML_SHA256
        head = client.head("/management.html")
        assert head.status_code == 200
        assert head.content == b""
        assert int(head.headers["content-length"]) == len(response.content)
        license_response = client.get("/management.LICENSE")
        assert license_response.status_code == 200
        assert "MIT License" in license_response.text
        assert "Router-For.ME" in license_response.text


@pytest.mark.parametrize("prefix", ["/v8/management", "/v0/management"])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"},
                                    {"Authorization": "Bearer test-key"}, {"X-Management-Key": "anything"}])
def test_management_never_authenticates(app, prefix, headers):
    with TestClient(app) as client:
        for path in (prefix, prefix + "/", prefix + "/config", prefix + "/config.yaml", prefix + "/auth-files"):
            for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                response = client.request(method, path, headers=headers, content=b'{"secret":"not-saved"}')
                assert response.status_code == 403
                assert response.json() == {"error": "remote management is disabled"}
                assert response.headers["cache-control"] == "no-store"


def test_anonymous_proxy_access_does_not_enable_management(config):
    config.access.api_keys = []
    with TestClient(create_app(config)) as client:
        assert client.get("/v1/models").status_code == 200
        assert client.get("/v8/management/config").status_code == 403


def test_management_over_real_http(network_client):
    page = network_client.get("/management.html")
    assert page.status_code == 200
    assert hashlib.sha256(page.content).hexdigest() == HTML_SHA256
    response = network_client.get("/v8/management/config", headers={"Authorization": "Bearer anything"})
    assert response.status_code == 403
    assert response.json()["error"] == "remote management is disabled"
