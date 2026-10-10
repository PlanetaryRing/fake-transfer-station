import asyncio
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from fake_transfer_station.request_log import RequestRecorder


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("path,body", [
    ("/v1/chat/completions", {"model": "gpt-test", "messages": [{"role": "user", "content": "你好\n奶龙"}],
                              "tools": [{"type": "function", "function": {"name": "test"}}], "stream": True}),
    ("/v1/completions", {"model": "gpt-test", "prompt": "你好"}),
    ("/v1/messages", {"model": "claude-test", "messages": [], "system": "测试", "max_tokens": 10}),
    ("/v1/messages/count_tokens", {"model": "claude-test", "messages": []}),
    ("/v1/responses", {"model": "gpt-test", "input": "你好", "stream": True}),
    ("/backend-api/codex/responses", {"model": "gpt-test", "input": []}),
    ("/v1/responses/compact", {"model": "gpt-test", "input": []}),
    ("/v1beta/models/gpt-test:generateContent", {"contents": [{"parts": [{"text": "你好"}]}]}),
])
def test_records_original_body_without_headers_or_query(client, request_log_path, path, body):
    response = client.post(path + "?key=test-key", json=body, headers={"X-Private-Header": "secret-header"})
    assert response.status_code == 200
    logged = records(request_log_path)
    assert len(logged) == 1
    assert logged[0]["body"] == body
    assert logged[0]["path"] == path
    assert datetime.fromisoformat(logged[0]["time"].replace("Z", "+00:00")).tzinfo is not None
    assert set(logged[0]) == {"time", "path", "body"}
    text = request_log_path.read_text(encoding="utf-8")
    assert "test-key" not in text and "secret-header" not in text


def test_append_and_invalid_input(client, request_log_path):
    body = {"model": "missing", "input": "中文😄"}
    assert client.post("/v1/responses", json=body).status_code == 502
    assert client.post("/v1/responses", content=b"{bad JSON").status_code == 400
    logged = records(request_log_path)
    assert len(logged) == 2
    assert logged[0]["body"] == body
    assert logged[1]["body"] == "{bad JSON"
    assert "中文😄" in request_log_path.read_text(encoding="utf-8")


def test_no_logging_discovery_management_or_rejected_auth(app, request_log_path):
    with TestClient(app) as client:
        for path in ("/", "/healthz", "/management.html", "/v8/management/config", "/v1/models"):
            client.get(path)
        client.post("/v1/responses", json={"model": "gpt-test", "input": "unauthorized"})
        client.post("/v0/management/config", json={"password": "do-not-save"})
        client.options("/v1/responses")
    assert not request_log_path.exists()


def test_websocket_records_each_original_generation(client, request_log_path):
    first = {"type": "response.create", "model": "gpt-test", "input": [], "generate": False}
    second = {"type": "response.append", "input": [{"role": "user", "content": "第二轮"}]}
    with client.websocket_connect("/v1/responses") as socket:
        for body in (first, second):
            socket.send_json(body)
            while socket.receive_json()["type"] != "response.completed":
                pass
    assert [row["body"] for row in records(request_log_path)] == [first, second]


async def test_concurrent_records_do_not_interleave(request_log_path):
    recorders = [RequestRecorder(), RequestRecorder()]
    bodies = [{"index": i, "input": "奶龙\n" * 1000} for i in range(40)]
    await asyncio.gather(*(recorders[i % 2].record("/v1/responses", json.dumps(body))
                           for i, body in enumerate(bodies)))
    logged = records(request_log_path)
    assert len(logged) == len(bodies)
    assert sorted((row["body"] for row in logged), key=lambda body: body["index"]) == bodies


def test_log_write_failure_does_not_break_generation(client, monkeypatch, caplog):
    def fail(self, line):
        raise PermissionError("read-only disk")

    monkeypatch.setattr(RequestRecorder, "_append", fail)
    response = client.post("/v1/responses", json={"model": "gpt-test", "input": "private-body"})
    assert response.status_code == 200
    assert "Could not append request log" in caplog.text
    assert "private-body" not in caplog.text
