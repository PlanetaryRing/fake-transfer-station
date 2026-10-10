import json

import pytest
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.responses import Response
from anthropic.types import Message

from tests.conftest import TEXT


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"]


@pytest.mark.parametrize("path,fields,extract", [
    ("/v1/chat/completions", {"messages": []}, lambda body: body["choices"][0]["message"]["content"]),
    ("/v1/completions", {"prompt": "hello"}, lambda body: body["choices"][0]["text"]),
    ("/v1/messages", {"messages": [], "max_tokens": 100}, lambda body: body["content"][0]["text"]),
    ("/v1/responses", {"input": "hello"}, lambda body: body["output"][0]["content"][0]["text"]),
    ("/backend-api/codex/responses", {"input": []}, lambda body: body["output"][0]["content"][0]["text"]),
    ("/v1beta/models/gpt-test:generateContent", {"contents": []}, lambda body: body["candidates"][0]["content"]["parts"][0]["text"]),
])
def test_nonstream_exact_text(client, path, fields, extract):
    response = client.post(path, json={"model": "gpt-test", **fields})
    assert response.status_code == 200
    assert extract(response.json()) == TEXT
    assert response.headers["x-request-id"].startswith("req_")


def test_sdk_schema_validation(client):
    chat = client.post("/v1/chat/completions", json={"model": "gpt-test", "messages": []}).json()
    assert ChatCompletion.model_validate(chat).choices[0].message.content == TEXT
    response = client.post("/v1/responses", json={"model": "gpt-test", "input": []}).json()
    assert Response.model_validate(response).output_text == TEXT
    message = client.post("/v1/messages", json={"model": "gpt-test", "messages": [], "max_tokens": 100}).json()
    assert Message.model_validate(message).content[0].text == TEXT


def test_chat_stream_and_usage(client):
    response = client.post("/v1/chat/completions", json={"model": "gpt-test", "messages": [], "stream": True,
                                                       "stream_options": {"include_usage": True}})
    chunks = events(response)
    for chunk in chunks:
        ChatCompletionChunk.model_validate(chunk)
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks if chunk["choices"]) == TEXT
    assert chunks[-2]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["choices"] == []
    usage = chunks[-1]["usage"]
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    assert response.text.endswith("data: [DONE]\n\n")
    assert len({chunk["id"] for chunk in chunks}) == 1


def test_responses_event_lifecycle(client):
    response = client.post("/v1/responses", json={"model": "gpt-test", "input": "hello", "stream": True})
    stream = events(response)
    kinds = [event["type"] for event in stream]
    assert kinds[:4] == ["response.created", "response.in_progress", "response.output_item.added", "response.content_part.added"]
    assert kinds[-4:] == ["response.output_text.done", "response.content_part.done", "response.output_item.done", "response.completed"]
    assert [event["sequence_number"] for event in stream] == list(range(len(stream)))
    assert "".join(event["delta"] for event in stream if event["type"] == "response.output_text.delta") == TEXT
    assert stream[-1]["response"]["output"][0] == stream[-2]["item"]
    assert stream[0]["response"]["id"] == stream[-1]["response"]["id"]


def test_messages_event_lifecycle(client):
    response = client.post("/v1/messages", json={"model": "claude-test", "messages": [], "max_tokens": 100, "stream": True})
    stream = events(response)
    assert stream[0]["type"] == "message_start"
    assert stream[0]["message"]["content"] == []
    assert stream[1]["type"] == "content_block_start"
    assert [event["type"] for event in stream[-3:]] == ["content_block_stop", "message_delta", "message_stop"]
    assert "".join(event["delta"]["text"] for event in stream if event["type"] == "content_block_delta") == TEXT


def test_gemini_stream_and_counts(client):
    response = client.post("/v1beta/models/gpt-test:streamGenerateContent?alt=sse", json={"contents": []})
    stream = events(response)
    assert "".join(event["candidates"][0]["content"]["parts"][0]["text"] for event in stream) == TEXT
    assert stream[-1]["candidates"][0]["finishReason"] == "STOP"
    assert "usageMetadata" in stream[-1]
    count = client.post("/v1/messages/count_tokens", json={"model": "gpt-test", "messages": []}).json()
    message = client.post("/v1/messages", json={"model": "gpt-test", "messages": [], "max_tokens": 100}).json()
    assert count["input_tokens"] == message["usage"]["input_tokens"]


def test_compaction_contract(client):
    payload = {"model": "gpt-test", "input": [{"role": "user", "content": "hello"},
                                               {"role": "assistant", "content": "old"}]}
    for path in ("/v1/responses/compact", "/backend-api/codex/responses/compact"):
        response = client.post(path, json=payload)
        assert response.status_code == 200
        result = response.json()
        assert result["object"] == "response.compaction"
        assert result["output"][0] == payload["input"][0]
        assert result["output"][-1]["type"] == "compaction"
        assert result["output"][-1]["encrypted_content"].startswith("prank_")
        assert client.post("/v1/responses", json={"model": "gpt-test", "input": result["output"]}).status_code == 200
        assert client.post(path, json={**payload, "stream": True}).status_code == 400


def test_malformed_requests_and_model_errors(client, config):
    for raw in (b"{", b"[]", b"null", b'{"stream": NaN}', b'{"stream": "true"}',
                b'{"input": "\\ud800"}', b'{"temperature": 1e999}'):
        assert client.post("/v1/responses", content=raw).status_code == 400
    assert client.post("/v1/messages", json={"model": "gpt-test", "messages": [None]}).status_code == 400
    assert client.post("/v1/responses", json={"input": []}).status_code == 400
    assert client.post("/v1/responses", json={"model": "missing", "input": []}).status_code == 502
    config.prank.max_request_bytes = 10
    assert client.post("/v1/responses", content=b"x" * 11).status_code == 413


def test_extensions_and_responses_on_chat_endpoint(client):
    response = client.post("/v1/chat/completions", json={"model": "gpt-test", "input": "hi",
        "tools": [{"type": "function", "name": "shell", "parameters": {}}],
        "reasoning": {"effort": "high"}, "vendor_extension": {"anything": True}})
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == TEXT
