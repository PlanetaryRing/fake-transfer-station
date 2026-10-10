import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from fake_transfer_station.app import create_app
from fake_transfer_station.config import Config

TEXT = "  /\\_/\\\n ( 奶龙😄 )\r\n  > ^ <\n"


@pytest.fixture
def config():
    return Config.model_validate({
        "access": {"api-keys": ["test-key", "second-key"]},
        "prank": {
            "streaming": {"initial-delay-ms": 0, "chunk-size": 2, "keepalive-seconds": 0},
            "models": [
                {"id": "gpt-test", "aliases": ["alias/test"], "display-name": "A GPT", "streaming": {"tokens-per-second": 0}},
                {"id": "claude-test", "display-name": "B Claude", "streaming": {"tokens-per-second": 0}},
            ],
            "content-pools": {"funny": {"items": [{"text": TEXT}]}},
        },
    })


@pytest.fixture
def app(config):
    return create_app(config)


@pytest.fixture
def client(app):
    with TestClient(app, headers={"Authorization": "Bearer test-key"}) as client:
        yield client


@pytest.fixture
def live_server(app):
    """Real TCP transport: SDKs and timing tests must not use buffered ASGITransport."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                         log_level="error", access_log=False, lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("test server failed to start")
            time.sleep(.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("test server did not shut down")


@pytest.fixture
def network_client(live_server):
    with httpx.Client(base_url=live_server, headers={"Authorization": "Bearer test-key"},
                      trust_env=False, timeout=10) as client:
        yield client
