import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from fake_transfer_station.app import create_app
from fake_transfer_station.config import Config, ContentItem, load_config
from fake_transfer_station.content import ContentEngine, claude_id, estimate_tokens
from fake_transfer_station.matching import last_user_text, model_identity, render_content


@pytest.fixture
def rules_config():
    config = load_config(Path(__file__).parents[1] / "config.rules.example.yaml")
    config.access.api_keys = ["test-key"]
    config.prank.streaming.initial_delay_ms = 0
    for model in config.prank.models:
        model.streaming.tokens_per_second = 0
        model.streaming.tokens_per_second_offset = 0
    return config


@pytest.fixture
def app(rules_config):
    return create_app(rules_config)


@pytest.mark.parametrize("text,pool", [
    ("hello", "greetings"), (" HI! ", "greetings"), ("你好！", "greetings"), ("嗨", "greetings"),
    ("ping", "ping"), ("PiNg?", "ping"), ("1+1", "arithmetic"), ("1 + 1 = ?", "arithmetic"),
    ("11+1", "funny"), ("this", "funny"), ("shipping", "funny"), ("写一个程序", "funny"),
])
def test_example_matching(rules_config, text, pool):
    g = ContentEngine(rules_config).generate({"model": "gpt-6-astra", "input": text})
    assert g.text in {item.text for item in rules_config.prank.content_pools[pool].items}


@pytest.mark.parametrize("model,identity", [
    ("gpt-6-astra", "ChatGPT"), ("my-gpt", "ChatGPT"), ("my-gpt(high)", "ChatGPT"),
    ("claude-opus", "Claude"), ("gemini-prank", "Gemini"),
    (claude_id("gpt-6-astra"), "ChatGPT"),
])
def test_identity_uses_resolved_model(rules_config, model, identity):
    g = ContentEngine(rules_config).generate({"model": model, "input": "你好，你是什么模型？"})
    assert identity in g.text
    assert model not in g.text
    assert g.output_tokens == estimate_tokens(g.text)


def test_auto_identity_and_literal_substitution(rules_config):
    for model, expected in zip(rules_config.prank.models, ["ChatGPT", "Claude", "Gemini"]):
        model.identity = None
        assert model_identity(model) == expected
    model = rules_config.prank.models[0]
    model.id = "unknown"
    assert model_identity(model) == "AI 助手"
    model.identity = "Custom {{identity}} \\1"
    assert render_content(ContentItem(template="我是 {{ identity }}"), model) == "我是 Custom {{identity}} \\1"
    assert render_content(ContentItem(text="{{identity}}"), model) == "{{identity}}"


@pytest.mark.parametrize("payload,expected", [
    ({"messages": [{"role": "user", "content": "hello"}, {"role": "user", "content": "new"}]}, "new"),
    ({"messages": [{"role": "user", "content": "ping"}, {"role": "assistant", "content": "hello"}]}, "ping"),
    ({"messages": [{"role": "system", "content": "hello"}, {"role": "tool", "content": "ping"}]}, ""),
    ({"messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}, {"type": "tool_result", "text": "ping"}]}]}, "hello\nworld"),
    ({"messages": [{"role": "user", "content": "hello"}, {"role": "user", "content": [{"type": "image", "text": "ping"}]}]}, ""),
    ({"input": [{"type": "function_call_output", "role": "user", "output": "hello"}]}, ""),
    ({"input": [{"role": "user", "content": [{"type": "input_text", "text": "ping"}]}]}, "ping"),
    ({"input": "hello"}, "hello"), ({"prompt": "ping"}, "ping"), ({"prompt": ["ping"]}, ""),
    ({"contents": [{"parts": [{"text": "hello"}, {"text": "hidden", "thought": True}]}]}, "hello"),
    ({"contents": [{"role": "user", "parts": [{"text": "ping"}]}, {"role": "model", "parts": [{"text": "hello"}]}]}, "ping"),
    ({"messages": [], "input": "hello", "instructions": "ping"}, ""),
])
def test_latest_user_extraction(payload, expected):
    assert last_user_text(payload) == expected


def test_priority_ties_weighting_thinking_and_fallback(config):
    raw = config.model_dump(by_alias=True)
    raw["prank"]["content-pools"].update({
        "low": {"items": [{"text": "low"}]},
        "high": {"items": [{"template": "{{identity}}", "weight": 3, "thinking": {"items": [{"text": "selected thinking"}]}}, {"text": "alternate", "weight": 2}]},
    })
    raw["prank"]["match-rules"] = [
        {"id": "low", "priority": 1, "pattern": "hello", "content-pool": "low"},
        {"id": "first-high", "priority": 10, "pattern": "hello", "content-pool": "high"},
        {"id": "second-high", "priority": 10, "pattern": "hello", "content-pool": "low"},
    ]
    config = Config.model_validate(raw)
    rng = Mock()
    selected = config.prank.content_pools["high"].items[0]
    rng.choices.side_effect = [[selected], [selected.thinking.items[0]]]
    engine = ContentEngine(config, rng)
    g = engine.generate({"model": "gpt-test", "input": "hello", "reasoning_effort": "high"})
    assert g.text == "ChatGPT"
    assert g.thinking_text == "selected thinking"
    assert rng.choices.call_args_list[0].args[0] == config.prank.content_pools["high"].items
    assert rng.choices.call_args_list[0].kwargs["weights"] == [3, 2]
    assert g.settings.tokens_per_second == 0
    rng.reset_mock()
    assert engine.generate({"model": "gpt-test", "input": "hello"}, prewarm=True).text == ""
    rng.choices.assert_not_called()
    engine = ContentEngine(config)
    expected = config.prank.content_pools["funny"].items[0].text
    assert engine.generate({"model": "gpt-test", "messages": [{"role": "user", "content": "hello"}, {"role": "user", "content": "other"}]}).text == expected
    config.prank.models[0].content_pool = "low"
    assert engine.generate({"model": "gpt-test", "input": "other"}).text == "low"


@pytest.mark.parametrize("changes", [
    {"pattern": "["}, {"flags": ["invalid"]}, {"content-pool": "missing"},
])
def test_invalid_rules(config, changes):
    raw = config.model_dump(by_alias=True)
    raw["prank"]["match-rules"] = [{"id": "rule", "pattern": "hello", "content-pool": "funny", **changes}]
    with pytest.raises(ValidationError):
        Config.model_validate(raw)


def test_duplicate_rule_ids_and_template_validation(config):
    raw = config.model_dump(by_alias=True)
    raw["prank"]["match-rules"] = [{"id": "rule", "pattern": "hello", "content-pool": "funny"}] * 2
    with pytest.raises(ValidationError, match="duplicate rule"):
        Config.model_validate(raw)
    for item in ({"template": ""}, {"template": "{{model}}"}, {"template": "{{identity",},
                 {"template": "{{identity}}", "text": "also text"}):
        with pytest.raises(ValidationError):
            ContentItem.model_validate(item)


@pytest.mark.parametrize("path,body,extract", [
    ("/v1/chat/completions", {"messages": [{"role": "user", "content": "ping"}]}, lambda r: r["choices"][0]["message"]["content"]),
    ("/v1/messages", {"messages": [{"role": "user", "content": "ping"}]}, lambda r: r["content"][0]["text"]),
    ("/v1/completions", {"prompt": "ping"}, lambda r: r["choices"][0]["text"]),
    ("/v1/responses", {"input": "ping"}, lambda r: r["output"][0]["content"][0]["text"]),
    ("/backend-api/codex/responses", {"input": "ping"}, lambda r: r["output"][0]["content"][0]["text"]),
    ("/v1beta/models/gpt-6-astra:generateContent", {"contents": [{"parts": [{"text": "ping"}]}]}, lambda r: r["candidates"][0]["content"]["parts"][0]["text"]),
])
def test_protocols_use_same_rules(client, path, body, extract):
    result = client.post(path, json={"model": "gpt-6-astra", **body})
    assert result.status_code == 200
    assert extract(result.json()) == "pong"


def test_stream_template_and_websocket(client, rules_config, request_log_path):
    reply = ContentItem(template="我是 {{identity}}")
    rules_config.prank.content_pools["identity-replies"].items = [reply]
    response = client.post("/v1/chat/completions", json={"model": "my-gpt", "messages": [{"role": "user", "content": "你是谁"}], "stream": True})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]
    assert "".join(e["choices"][0]["delta"].get("content", "") for e in events) == "我是 ChatGPT"
    with client.websocket_connect("/v1/responses") as socket:
        for text, expected in (("你是谁", "我是 Claude"), ("ping", "pong")):
            socket.send_json({"type": "response.create", "model": "claude-opus", "input": [{"role": "user", "content": text}]})
            parts = []
            while True:
                event = socket.receive_json()
                if event["type"] == "response.output_text.delta":
                    parts.append(event["delta"])
                if event["type"] == "response.completed":
                    break
            assert "".join(parts) == expected
    logged = [json.loads(line) for line in request_log_path.read_text(encoding="utf-8").splitlines()]
    assert logged[0]["body"]["messages"][0]["content"] == "你是谁"
