import asyncio
import random
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from pydantic import ValidationError

from fake_transfer_station.config import Config, ResolvedStreaming, load_config
from fake_transfer_station.content import ContentEngine, claude_id
from fake_transfer_station.streaming import with_heartbeats


def write_config(tmp_path, value):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    return path


def test_example_config_loads():
    config = load_config(Path(__file__).parents[1] / "config.example.yaml")
    assert config.prank.models
    assert all(item.text for pool in config.prank.content_pools.values() for item in pool.items)


def test_relative_file_preserves_bytes(config, tmp_path):
    raw = config.model_dump(by_alias=True)
    text = "  奶龙\\\r\n\n  😄 "
    (tmp_path / "art.txt").write_bytes(text.encode("utf-8"))
    raw["prank"]["content-pools"]["funny"]["items"] = [{"file": "art.txt"}]
    loaded = load_config(write_config(tmp_path, raw))
    assert loaded.prank.content_pools["funny"].items[0].text == text


@pytest.mark.parametrize("mutation", [
    lambda raw: raw["prank"]["models"].append({"id": "gpt-test", "streaming": {"tokens-per-second": 0}}),
    lambda raw: raw["prank"]["models"][0].update({"content-pool": "missing"}),
    lambda raw: raw["prank"]["content-pools"]["funny"].update({"items": []}),
    lambda raw: raw["prank"]["content-pools"]["funny"]["items"][0].update({"weight": 0}),
    lambda raw: raw["prank"]["content-pools"]["funny"]["items"][0].update({"file": "art.txt"}),
    lambda raw: raw["prank"]["streaming"].update({"chunk-size": 0}),
    lambda raw: raw["prank"]["streaming"].update({"tokens-per-second": 30}),
    lambda raw: raw["prank"]["models"][0].update({"streaming": {"tokens-per-second": float("inf")}}),
    lambda raw: raw["prank"]["models"][0].update({"streaming": {"tokens-per-second": -1}}),
    lambda raw: raw["prank"]["models"][0].pop("streaming"),
    lambda raw: raw["prank"]["models"][0].update({"streaming": {"chunk-size": 2}}),
    lambda raw: raw["prank"]["models"][0].update({"streaming": {"tokens-per-second": 10, "tokens-per-second-offset": 10}}),
    lambda raw: raw["prank"]["models"][0].update({"streaming": {"tokens-per-second": 10, "tokens-per-second-offset": 11}}),
    lambda raw: raw["prank"]["models"][0].update({"streaming": {"tokens-per-second": 0, "tokens-per-second-offset": 1}}),
    lambda raw: raw["prank"]["models"][0].update({"streaming": {"tokens-per-second": 10, "tokens-per-second-offset": -1}}),
    lambda raw: raw["prank"]["models"][0].update({"streaming": {"tokens-per-second": 10, "tokens-per-second-offset": float("nan")}}),
    lambda raw: raw["prank"].update({"typo-setting": True}),
])
def test_invalid_config_rejected(config, mutation):
    raw = config.model_dump(by_alias=True)
    mutation(raw)
    with pytest.raises(ValidationError):
        Config.model_validate(raw)


def test_missing_file_and_duplicate_yaml(config, tmp_path):
    raw = config.model_dump(by_alias=True)
    raw["prank"]["content-pools"]["funny"]["items"] = [{"file": "missing.txt"}]
    with pytest.raises(ValueError, match="content pool funny"):
        load_config(write_config(tmp_path, raw))
    path = tmp_path / "duplicate.yaml"
    path.write_text("server: {}\nserver: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate YAML key"):
        load_config(path)


def test_legacy_settings_and_v8_precedence(config, tmp_path):
    raw = config.model_dump(by_alias=True)
    raw["api-keys"] = ["legacy"]
    raw["port"] = 1234
    raw["access"]["api-keys"] = ["  test-key  ", "", "test-key"]
    raw["server"]["port"] = 8317
    loaded = load_config(write_config(tmp_path, raw))
    assert loaded.access.api_keys == ["test-key"]
    assert loaded.server.port == 8317
    del raw["access"]
    del raw["server"]
    loaded = load_config(write_config(tmp_path, raw))
    assert loaded.access.api_keys == ["legacy"]
    assert loaded.server.port == 1234


async def test_selection_once_per_request_and_override(config):
    raw = config.model_dump(by_alias=True)
    raw["prank"]["models"][0]["streaming"].update({"chunk-size": 1})
    raw["prank"]["content-pools"]["funny"]["items"] = [{"text": "abc", "weight": 1}, {"text": "😄奶龙", "weight": 4}]
    config = Config.model_validate(raw)
    rng = Mock(wraps=random.Random(42))
    engine = ContentEngine(config, rng)
    generation = engine.generate({"model": "gpt-test", "input": "hi"})
    chunks = [part async for part in generation.chunks()]
    assert "".join(chunks) in ("abc", "😄奶龙")
    assert all(len(part) == 1 for part in chunks)
    rng.choices.assert_called_once()
    assert rng.choices.call_args.kwargs["weights"] == [1, 4]
    assert engine.resolve("alias/test") is config.prank.models[0]
    assert engine.resolve(claude_id("gpt-test") + "(high)") is config.prank.models[0]


async def test_cancellation_and_heartbeats():
    closed = asyncio.Event()

    async def slow_source():
        try:
            await asyncio.sleep(60)
            yield "unused"
        finally:
            closed.set()

    frames = with_heartbeats(slow_source(), .01)
    assert await anext(frames) == ": keepalive\n\n"
    await frames.aclose()
    assert closed.is_set()


async def test_unicode_chunk_roundtrip(config):
    engine = ContentEngine(config)
    g = engine.generate({"model": "gpt-test"})
    for size in (1, 2, 7, 1000):
        copy = replace(g, settings=ResolvedStreaming(initial_delay_ms=0, tokens_per_second=0, chunk_size=size))
        assert "".join([text async for text in copy.chunks()]) == g.text


@pytest.mark.parametrize("text", ["abcdefg", "奶龙哈哈😄", "a奶😄\r\nxyz"])
@pytest.mark.parametrize("size", [1, 3, 1000])
async def test_token_rate_total_is_independent_of_chunks(config, monkeypatch, text, size):
    from fake_transfer_station import content

    generation = ContentEngine(config).generate({"model": "gpt-test"})
    generation = replace(generation, text=text,
                         settings=ResolvedStreaming(initial_delay_ms=200, tokens_per_second=2.5, chunk_size=size))
    clock = [100.0]

    async def sleep(delay):
        assert delay >= 0
        clock[0] += delay

    # Replace only module references, not asyncio's or the event loop's clock.
    monkeypatch.setattr(content, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(content, "asyncio", SimpleNamespace(sleep=sleep))
    chunks = []
    async for chunk in generation.chunks():
        chunks.append(chunk)
        byte_cost = len("".join(chunks).encode("utf-8")) / 4
        assert clock[0] >= 100.2 + byte_cost / 2.5 - 1e-9
    assert "".join(chunks) == text
    assert clock[0] == pytest.approx(100.2 + content.estimate_tokens(text) / 2.5)


async def test_deadlines_compensate_for_send_overhead(config, monkeypatch):
    from fake_transfer_station import content

    generation = replace(ContentEngine(config).generate({"model": "gpt-test"}), text="abcdefghijkl",
                         settings=ResolvedStreaming(initial_delay_ms=0, tokens_per_second=2, chunk_size=4))
    clock = [0.0]

    async def sleep(delay):
        clock[0] += delay

    monkeypatch.setattr(content, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(content, "asyncio", SimpleNamespace(sleep=sleep))
    arrivals = []
    async for chunk in generation.chunks():
        arrivals.append(clock[0])
        clock[0] += .1  # Simulated send/consumer overhead must not accumulate.
    assert arrivals == pytest.approx([.5, 1.0, 1.5])


def test_model_speed_independent_and_aliases_resolve(config):
    config.prank.models[1].streaming.tokens_per_second = 45
    config.prank.models[0].streaming.tokens_per_second = 12.5
    config.prank.models[0].streaming.tokens_per_second_offset = 2.5
    engine = ContentEngine(config)
    before = engine.generate({"model": "gpt-test"})
    assert engine.generate({"model": "gpt-test"}).settings.tokens_per_second == 12.5
    assert engine.generate({"model": "claude-test"}).settings.tokens_per_second == 45
    assert engine.generate({"model": "alias/test"}).settings.tokens_per_second_offset == 2.5
    assert engine.generate({"model": claude_id("gpt-test")}).settings.tokens_per_second == 12.5
    config.prank.models[0].streaming.tokens_per_second_offset = 0
    config.prank.models[0].streaming.tokens_per_second = 0
    assert engine.generate({"model": "gpt-test"}).settings.tokens_per_second == 0
    assert before.settings.tokens_per_second == 12.5
    assert before.settings.tokens_per_second_offset == 2.5


async def test_jitter_changes_chunk_speed_and_preserves_token_accounting(config, monkeypatch):
    from fake_transfer_station import content

    # UTF-8 lengths: 4, 3, 1, 1 -> token costs 1, .75, .25, 1 (final rounding).
    generation = replace(ContentEngine(config).generate({"model": "gpt-test"}), text="😄奶ab",
                         settings=ResolvedStreaming(initial_delay_ms=200, tokens_per_second=10,
                                                    tokens_per_second_offset=2, chunk_size=1))
    clock = [0.0]
    rng = Mock()
    rng.uniform.side_effect = [8, 12, 9, 11]

    async def sleep(delay):
        assert delay >= 0
        clock[0] += delay

    monkeypatch.setattr(content, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(content, "asyncio", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(content, "random", SimpleNamespace(SystemRandom=lambda: rng))
    times = [.2]
    chunks = []
    async for chunk in generation.chunks():
        chunks.append(chunk)
        times.append(clock[0])
    assert "".join(chunks) == generation.text
    costs = [1, .75, .25, 1]
    actual_rates = [cost / (end - start) for cost, start, end in zip(costs, times, times[1:])]
    assert actual_rates == pytest.approx([8, 12, 9, 11])
    assert all(call.args == (8, 12) for call in rng.uniform.call_args_list)
    assert .2 + 3 / 12 <= clock[0] <= .2 + 3 / 8


@pytest.mark.parametrize("rate", [0, 10])
async def test_zero_offset_never_samples_random_speed(config, monkeypatch, rate):
    from fake_transfer_station import content

    generation = replace(ContentEngine(config).generate({"model": "gpt-test"}), text="abcdef",
                         settings=ResolvedStreaming(initial_delay_ms=0, tokens_per_second=rate,
                                                    tokens_per_second_offset=0, chunk_size=2))
    factory = Mock(side_effect=AssertionError("unexpected speed RNG"))
    clock = [0.0]

    async def sleep(delay):
        clock[0] += delay

    monkeypatch.setattr(content, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(content, "asyncio", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(content, "random", SimpleNamespace(SystemRandom=factory))
    assert "".join([part async for part in generation.chunks()]) == "abcdef"
    assert clock[0] == pytest.approx(2 / rate if rate else 0)
    factory.assert_not_called()
