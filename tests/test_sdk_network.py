import asyncio
import json
import time
from contextlib import suppress

import pytest
from anthropic import Anthropic, DefaultHttpxClient as AnthropicHTTPClient
from openai import OpenAI, DefaultHttpxClient as OpenAIHTTPClient
from websockets.sync.client import connect

from tests.conftest import TEXT
from fake_transfer_station.streaming import CancellableStreamingResponse
from fake_transfer_station.content import estimate_tokens


def test_openai_sdk(live_server):
    with OpenAI(base_url=live_server + "/v1", api_key="test-key", max_retries=0,
                http_client=OpenAIHTTPClient(trust_env=False), timeout=10) as client:
        assert client.models.list().data[0].id == "gpt-test"
        assert client.chat.completions.create(model="gpt-test", messages=[]).choices[0].message.content == TEXT
        with client.chat.completions.create(model="gpt-test", messages=[], stream=True,
                                             stream_options={"include_usage": True}) as stream:
            chunks = list(stream)
        assert "".join(chunk.choices[0].delta.content or "" for chunk in chunks if chunk.choices) == TEXT
        assert chunks[-1].usage.completion_tokens > 0
        with client.chat.completions.stream(model="gpt-test", messages=[]) as stream:
            stream.until_done()
            assert stream.get_final_completion().choices[0].message.content == TEXT
        assert client.completions.create(model="gpt-test", prompt="hello").choices[0].text == TEXT
        assert client.responses.create(model="gpt-test", input="hello").output_text == TEXT
        with client.responses.stream(model="gpt-test", input="hello") as stream:
            deltas = [event.delta for event in stream if event.type == "response.output_text.delta"]
            assert "".join(deltas) == TEXT
            assert stream.get_final_response().output_text == TEXT
        compacted = client.responses.compact(model="gpt-test", input="hello")
        assert compacted.object == "response.compaction"
        assert compacted.output[-1].type == "compaction"


def test_anthropic_sdk(live_server):
    with Anthropic(base_url=live_server, api_key="test-key", max_retries=0,
                   http_client=AnthropicHTTPClient(trust_env=False), timeout=10) as client:
        models = client.models.list()
        assert models.data[0].type == "model"
        message = client.messages.create(model="claude-test", messages=[{"role": "user", "content": "hi"}], max_tokens=100)
        assert message.content[0].text == TEXT
        with client.messages.stream(model="claude-test", messages=[{"role": "user", "content": "hi"}], max_tokens=100) as stream:
            assert "".join(stream.text_stream) == TEXT
            assert stream.get_final_text() == TEXT
            assert stream.get_final_message().stop_reason == "end_turn"
        count = client.messages.count_tokens(model="claude-test", messages=[{"role": "user", "content": "hi"}])
        assert count.input_tokens == message.usage.input_tokens


def test_network_stream_is_not_buffered(network_client, config):
    config.prank.streaming.chunk_size = 4
    config.prank.streaming.initial_delay_ms = 80
    config.prank.models[0].streaming.tokens_per_second = 20
    arrivals = []
    texts = []
    start = time.monotonic()
    with network_client.stream("POST", "/v1/chat/completions", json={"model": "gpt-test", "messages": [], "stream": True}) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            body = json.loads(line[6:])
            text = body["choices"][0]["delta"].get("content", "")
            if text:
                texts.append(text)
                arrivals.append(time.monotonic())
    assert "".join(texts) == TEXT
    assert arrivals[0] - start >= .05
    first_cost = len(texts[0].encode("utf-8")) / 4
    expected = (estimate_tokens(TEXT) - first_cost) / 20
    assert arrivals[-1] - arrivals[0] >= expected * .8
    assert arrivals[-1] - start >= .08 + estimate_tokens(TEXT) / 20 * .8


@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
def test_websocket_turns_and_prewarm(live_server, path):
    url = live_server.replace("http://", "ws://") + path
    with connect(url, additional_headers={"Authorization": "Bearer test-key"}, proxy=None, open_timeout=5) as socket:
        def receive_turn():
            events = []
            while True:
                event = json.loads(socket.recv(timeout=5))
                events.append(event)
                assert event["type"] != "error", event
                if event["type"] == "response.completed":
                    return events

        socket.send(json.dumps({"type": "response.create", "model": "gpt-test", "input": [], "generate": False}))
        warm = receive_turn()
        assert warm[-1]["response"]["output"] == []
        parent = warm[-1]["response"]["id"]
        socket.send(json.dumps({"type": "response.create", "previous_response_id": parent, "input": []}))
        events = receive_turn()
        assert "".join(event["delta"] for event in events if event["type"] == "response.output_text.delta") == TEXT
        assert events[-1]["response"]["id"] != parent
        socket.send(json.dumps({"type": "response.append", "input": []}))
        assert receive_turn()[-1]["response"]["status"] == "completed"
        socket.send(json.dumps({"type": "response.create", "previous_response_id": "missing", "input": []}))
        assert json.loads(socket.recv(timeout=5))["error"]["code"] == "previous_response_not_found"


async def test_disconnect_cancels_sleep_immediately():
    started = asyncio.Event()
    closed = asyncio.Event()
    outgoing = []

    async def body():
        try:
            started.set()
            await asyncio.sleep(60)
            yield "unexpected"
        finally:
            closed.set()

    async def receive():
        await started.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        outgoing.append(message)

    response = CancellableStreamingResponse(body())
    await asyncio.wait_for(response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send), timeout=1)
    assert closed.is_set()
    assert all(not message.get("body") for message in outgoing)


async def test_two_slow_generators_run_concurrently(config):
    from fake_transfer_station.content import ContentEngine

    config.prank.streaming.initial_delay_ms = 0
    config.prank.models[0].streaming.tokens_per_second = 5
    config.prank.streaming.chunk_size = 1
    engine = ContentEngine(config)
    first = engine.generate({"model": "gpt-test"}).chunks()
    second = engine.generate({"model": "gpt-test"}).chunks()
    try:
        assert await asyncio.gather(anext(first), anext(second)) == [TEXT[0], TEXT[0]]
        # Event-loop callbacks must run while both generators are sleeping.
        tasks = [asyncio.create_task(anext(first)), asyncio.create_task(anext(second))]
        await asyncio.sleep(.005)
        assert all(not task.done() for task in tasks)
        assert await asyncio.gather(*tasks) == [TEXT[1], TEXT[1]]
    finally:
        for task in locals().get("tasks", []):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await first.aclose()
        await second.aclose()
