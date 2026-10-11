import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from anthropic import Anthropic, DefaultHttpxClient as AnthropicHTTPClient
from openai import OpenAI, DefaultHttpxClient as OpenAIHTTPClient
from openai.types.responses import Response
from pydantic import TypeAdapter, ValidationError
from openai.types.responses import ResponseStreamEvent

from fake_transfer_station.config import Config, ContentItem, load_config
from fake_transfer_station.content import ContentEngine, estimate_tokens
from fake_transfer_station.protocols import response_events
from fake_transfer_station.thinking import requested_effort

THOUGHT = "先想一下😄\n再派奶龙回答。"
ANSWER = "奶龙来了！"


@pytest.fixture
def thinking_config(config):
    config.prank.content_pools["funny"].items = [ContentItem.model_validate({
        "text": ANSWER, "thinking": {"items": [{"text": THOUGHT}]}})]
    return config


@pytest.fixture
def app(thinking_config):
    from fake_transfer_station.app import create_app
    return create_app(thinking_config)


@pytest.mark.parametrize("payload,expected", [
    ({}, None), ({"reasoning_effort": "none"}, "none"),
    ({"reasoning_effort": "high"}, "high"), ({"reasoning": {"effort": "low"}}, "low"),
    ({"model": "m(high)"}, "high"),
    ({"model": "m(none)", "reasoning_effort": "high"}, "high"),
    ({"model": "m(high)", "reasoning_effort": "none"}, "none"),
    ({"thinking": {"type": "enabled", "budget_tokens": 1024}}, "auto"),
    ({"thinking": {"type": "adaptive"}, "output_config": {"effort": "max"}}, "max"),
    ({"thinking": {"type": "disabled"}, "reasoning_effort": "high"}, "none"),
    ({"reasoning": {"effort": "none"}, "reasoning_effort": "high"}, "none"),
    ({"generationConfig": {"thinkingConfig": {"thinkingBudget": -1}}}, "auto"),
    ({"generationConfig": {"thinkingConfig": {"thinkingBudget": 0}}}, "none"),
    ({"generationConfig": {"thinkingConfig": {"thinkingLevel": "HIGH"}}}, "high"),
    ({"generationConfig": {"thinkingConfig": {"includeThoughts": True}}}, None),
    ({"generationConfig": {"thinkingConfig": {"includeThoughts": False, "thinkingLevel": "high"}}}, "none"),
])
def test_enable_rules(payload, expected):
    assert requested_effort(payload) == expected


@pytest.mark.parametrize("fields", [
    {"reasoning_effort": True}, {"reasoning_effort": "bogus"}, {"reasoning": []},
    {"thinking": {"type": "bogus"}}, {"thinking": {"type": "enabled", "budget_tokens": True}},
    {"generationConfig": {"thinkingConfig": {"thinkingBudget": -2}}},
    {"generationConfig": {"thinkingConfig": {"includeThoughts": "yes"}}},
])
def test_invalid_controls_return_400_before_stream(client, fields):
    result = client.post("/v1/responses", json={"model": "gpt-test", "input": [], "stream": True, **fields})
    assert result.status_code == 400
    assert result.headers["content-type"].startswith("application/json")


def test_bound_weighted_selection(config):
    a = ContentItem.model_validate({"text": "A", "weight": 3, "thinking": {"items": [
        {"text": "A1", "weight": 2}, {"text": "A2", "weight": 5}]}})
    b = ContentItem.model_validate({"text": "B", "thinking": {"items": [{"text": "B1"}]}})
    config.prank.content_pools["funny"].items = [a, b]
    rng = Mock()
    rng.choices.side_effect = [[a], [a.thinking.items[1]], [b], [b.thinking.items[0]]]
    engine = ContentEngine(config, rng)
    first = engine.generate({"model": "gpt-test", "reasoning_effort": "high"})
    second = engine.generate({"model": "gpt-test", "reasoning_effort": "high"})
    assert (first.text, first.thinking_text) == ("A", "A2")
    assert (second.text, second.thinking_text) == ("B", "B1")
    assert rng.choices.call_args_list[1].args[0] == a.thinking.items
    assert rng.choices.call_args_list[1].kwargs["weights"] == [2, 5]
    assert rng.choices.call_count == 4


@pytest.mark.parametrize("fields", [{}, {"reasoning_effort": "none"}])
def test_disabled_does_not_draw_thought(thinking_config, fields):
    rng = Mock()
    rng.choices.return_value = [thinking_config.prank.content_pools["funny"].items[0]]
    generation = ContentEngine(thinking_config, rng).generate({"model": "gpt-test", **fields})
    assert generation.thinking_text == ""
    assert generation.reasoning_tokens == 0
    rng.choices.assert_called_once()


def test_missing_thinking_and_prewarm(config, thinking_config):
    engine = ContentEngine(thinking_config)
    assert engine.generate({"model": "gpt-test", "reasoning_effort": "high"}, prewarm=True).thinking_text == ""
    config.prank.content_pools["funny"].items = [ContentItem(text="only answer")]
    assert ContentEngine(config).generate({"model": "gpt-test", "reasoning_effort": "high"}).thinking_text == ""


def test_thinking_files_and_validation(config, tmp_path):
    raw = config.model_dump(by_alias=True)
    raw["prank"]["content-pools"]["funny"]["items"] = [{"text": ANSWER, "thinking": {"items": [{"file": "thought.txt"}]}}]
    (tmp_path / "thought.txt").write_bytes(THOUGHT.encode("utf-8"))
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert load_config(path).prank.content_pools["funny"].items[0].thinking.items[0].text == THOUGHT
    (tmp_path / "thought.txt").unlink()
    with pytest.raises(ValueError, match="thinking 0"):
        load_config(path)
    for thinking in ({"items": []}, {"items": [{"text": "", "weight": 1}]},
                     {"items": [{"text": "a", "file": "b"}]}, {"items": [{"text": "a", "weight": 0}]}):
        with pytest.raises(ValidationError):
            ContentItem.model_validate({"text": ANSWER, "thinking": thinking})


def test_complete_thinking_example_loads():
    config = load_config(Path(__file__).parents[1] / "config.thinking.example.yaml")
    assert all(item.text and item.thinking and all(t.text for t in item.thinking.items)
               for item in config.prank.content_pools["funny"].items)


@pytest.mark.parametrize("fields", [{}, {"reasoning_effort": "none"}])
def test_all_protocols_omit_disabled_thinking(client, fields):
    chat = client.post("/v1/chat/completions", json={"model": "gpt-test", "messages": [], **fields}).json()
    assert "reasoning_content" not in chat["choices"][0]["message"]
    responses = client.post("/v1/responses", json={"model": "gpt-test", "input": [], **fields}).json()
    assert len(responses["output"]) == 1
    assert responses["usage"]["output_tokens_details"]["reasoning_tokens"] == 0
    message = client.post("/v1/messages", json={"model": "gpt-test", "messages": [], **fields}).json()
    assert message["content"] == [{"type": "text", "text": ANSWER}]
    gemini = client.post("/v1beta/models/gpt-test:generateContent", json={"contents": [], **fields}).json()
    assert gemini["candidates"][0]["content"]["parts"] == [{"text": ANSWER}]


def frames(response):
    return [json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"]


def test_chat_and_legacy(client):
    payload = {"model": "gpt-test", "messages": [], "reasoning_effort": "high"}
    response = client.post("/v1/chat/completions", json=payload).json()
    assert response["choices"][0]["message"]["reasoning_content"] == THOUGHT
    assert response["choices"][0]["message"]["content"] == ANSWER
    stream = frames(client.post("/v1/chat/completions", json={**payload, "stream": True}))
    deltas = [event["choices"][0]["delta"] for event in stream]
    assert "".join(delta.get("reasoning_content", "") for delta in deltas) == THOUGHT
    assert "".join(delta.get("content", "") for delta in deltas) == ANSWER
    assert max(i for i, d in enumerate(deltas) if "reasoning_content" in d) < min(i for i, d in enumerate(deltas) if d.get("content"))
    legacy = client.post("/v1/completions", json={"model": "gpt-test", "prompt": "hi", "reasoning_effort": "high"}).json()
    assert legacy["choices"][0]["text"] == ANSWER
    assert legacy["usage"]["completion_tokens"] == estimate_tokens(ANSWER)


async def test_responses_events_and_usage(thinking_config):
    g = ContentEngine(thinking_config).generate({"model": "gpt-test", "reasoning": {"effort": "high"}})
    stream = [event async for event in response_events(g)]
    adapter = TypeAdapter(ResponseStreamEvent)
    for event in stream:
        adapter.validate_python(event)
    final = Response.model_validate(stream[-1]["response"])
    assert final.output[0].summary[0].text == THOUGHT
    assert final.output_text == ANSWER
    assert final.usage.output_tokens_details.reasoning_tokens == estimate_tokens(THOUGHT)
    assert final.usage.output_tokens == estimate_tokens(THOUGHT) + estimate_tokens(ANSWER)
    assert [e["sequence_number"] for e in stream] == list(range(len(stream)))
    assert "".join(e["delta"] for e in stream if e["type"] == "response.reasoning_summary_text.delta") == THOUGHT
    assert next(e for e in stream if e["type"] == "response.output_text.delta")["output_index"] == 1


def test_anthropic_and_gemini(client):
    payload = {"model": "gpt-test", "messages": [], "thinking": {"type": "enabled", "budget_tokens": 1024}}
    result = client.post("/v1/messages", json=payload).json()
    assert result["content"][0]["thinking"] == THOUGHT
    assert result["content"][1]["text"] == ANSWER
    stream = frames(client.post("/v1/messages", json={**payload, "stream": True}))
    assert "".join(e["delta"]["thinking"] for e in stream if e.get("delta", {}).get("type") == "thinking_delta") == THOUGHT
    assert any(e.get("delta", {}).get("type") == "signature_delta" for e in stream)
    gemini = {"contents": [], "generationConfig": {"thinkingConfig": {"thinkingBudget": 1024, "includeThoughts": True}}}
    result = client.post("/v1beta/models/gpt-test:generateContent", json=gemini).json()
    assert result["candidates"][0]["content"]["parts"] == [{"text": THOUGHT, "thought": True}, {"text": ANSWER}]
    stream = frames(client.post("/v1beta/models/gpt-test:streamGenerateContent?alt=sse", json=gemini))
    parts = [e["candidates"][0]["content"]["parts"][0] for e in stream]
    assert "".join(p["text"] for p in parts if p.get("thought")) == THOUGHT
    assert "".join(p["text"] for p in parts if not p.get("thought")) == ANSWER


def test_thinking_sdk_aggregation(live_server):
    with OpenAI(base_url=live_server + "/v1", api_key="test-key", max_retries=0,
                http_client=OpenAIHTTPClient(trust_env=False)) as client:
        with client.responses.stream(model="gpt-test", input="hi", reasoning={"effort": "high"}) as stream:
            stream.until_done()
            result = stream.get_final_response()
            assert result.output[0].summary[0].text == THOUGHT
            assert result.output_text == ANSWER
    with Anthropic(base_url=live_server, api_key="test-key", max_retries=0,
                   http_client=AnthropicHTTPClient(trust_env=False)) as client:
        with client.messages.stream(model="gpt-test", messages=[], max_tokens=2048,
                                    thinking={"type": "enabled", "budget_tokens": 1024}) as stream:
            assert "".join(stream.text_stream) == ANSWER
            result = stream.get_final_message()
            assert result.content[0].thinking == THOUGHT
            assert result.content[0].signature.startswith("prank_")


def test_websocket_thinking_and_disable(client):
    with client.websocket_connect("/v1/responses") as socket:
        for level in ("high", "none"):
            socket.send_json({"type": "response.create", "model": "gpt-test", "input": [], "reasoning": {"effort": level}})
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event["type"] == "response.completed":
                    break
            thoughts = [e for e in events if e["type"] == "response.reasoning_summary_text.delta"]
            assert bool(thoughts) == (level == "high")


async def test_shared_speed_delay_once_and_jitter(thinking_config, monkeypatch):
    from fake_transfer_station import content
    model = thinking_config.prank.models[0]
    model.streaming.tokens_per_second = 10
    model.streaming.tokens_per_second_offset = 2
    model.streaming.initial_delay_ms = 300
    g = ContentEngine(thinking_config).generate({"model": "gpt-test", "reasoning_effort": "high"})
    clock = [0.0]
    delays = []
    rng = Mock()
    rng.uniform.side_effect = lambda low, high: low

    async def sleep(delay):
        delays.append(delay)
        clock[0] += delay

    monkeypatch.setattr(content, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(content, "asyncio", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(content, "random", SimpleNamespace(SystemRandom=lambda: rng))
    assert "".join([t async for t in g.chunks(thinking=True)]) == THOUGHT
    assert "".join([t async for t in g.chunks()]) == ANSWER
    assert clock[0] == pytest.approx(.3 + g.total_output_tokens / 8)
    assert all(call.args == (8, 12) for call in rng.uniform.call_args_list)


async def test_cancel_during_thinking(thinking_config):
    thinking_config.prank.models[0].streaming.tokens_per_second = .01
    g = ContentEngine(thinking_config).generate({"model": "gpt-test", "reasoning_effort": "high"})
    chunks = g.chunks(thinking=True)
    task = asyncio.create_task(anext(chunks))
    await asyncio.sleep(.01)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await chunks.aclose()
