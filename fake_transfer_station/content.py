import asyncio
import json
import math
import random
import time
from dataclasses import dataclass
from uuid import uuid4

from .config import Config, Model, ResolvedStreaming
from .errors import APIError
from .thinking import requested_effort

CLAUDE_PREFIX = "claude-fable-5-dd-"


def claude_id(name: str) -> str:
    return name if name.startswith("claude-") else CLAUDE_PREFIX + name[::-1]


def estimate_tokens(value) -> int:
    """Deliberate mock accounting: ceil(UTF-8 bytes / 4), not a model tokenizer."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return math.ceil(len(text.encode("utf-8")) / 4)


def input_tokens(payload: dict) -> int:
    fields = {key: payload[key] for key in ("messages", "input", "prompt", "system", "instructions", "tools", "contents", "systemInstruction") if key in payload}
    return estimate_tokens(fields) if fields else 0


@dataclass(frozen=True)
class Generation:
    model: str
    text: str
    settings: ResolvedStreaming
    input_tokens: int
    output_tokens: int
    id: str
    message_id: str
    created: int
    thinking_text: str = ""
    reasoning_effort: str | None = None

    @property
    def reasoning_tokens(self) -> int:
        return estimate_tokens(self.thinking_text)

    @property
    def total_output_tokens(self) -> int:
        return self.output_tokens + self.reasoning_tokens

    async def chunks(self, *, thinking: bool = False):
        source = self.thinking_text if thinking else self.text
        if thinking or not self.thinking_text:
            await asyncio.sleep(self.settings.initial_delay_ms / 1000)
        deadline = time.monotonic()
        emitted_bytes = 0
        previous_tokens = 0
        rate = self.settings.tokens_per_second
        jitter = self.settings.tokens_per_second_offset
        # Per-iterator randomness; concurrent requests do not share pacing state
        # or consume the content-selection RNG.
        rng = random.SystemRandom() if rate and jitter else None
        for offset in range(0, len(source), self.settings.chunk_size):
            text = source[offset:offset + self.settings.chunk_size]
            emitted_bytes += len(text.encode("utf-8"))
            # Carry fractional estimated tokens across chunks; rounding each chunk
            # would make the rate depend on chunk_size. Round only at the end,
            # matching the output_tokens returned in the final usage object.
            tokens = emitted_bytes / 4
            if offset + len(text) == len(source):
                tokens = math.ceil(tokens)
            if rate:
                block_rate = rng.uniform(rate - jitter, rate + jitter) if rng else rate
                deadline += (tokens - previous_tokens) / block_rate
            previous_tokens = tokens
            delay = max(0, deadline - time.monotonic()) if rate else 0
            # Absolute deadlines avoid accumulating scheduling/send overhead.
            await asyncio.sleep(delay)
            yield text

    def chat_usage(self) -> dict:
        result = {"prompt_tokens": self.input_tokens, "completion_tokens": self.total_output_tokens,
                  "total_tokens": self.input_tokens + self.total_output_tokens}
        if self.thinking_text:
            result["completion_tokens_details"] = {"reasoning_tokens": self.reasoning_tokens}
        return result

    def response_usage(self) -> dict:
        return {"input_tokens": self.input_tokens, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "output_tokens": self.total_output_tokens, "output_tokens_details": {"reasoning_tokens": self.reasoning_tokens},
                "total_tokens": self.input_tokens + self.total_output_tokens}


class ContentEngine:
    def __init__(self, config: Config, rng=None):
        self.config = config
        self.rng = rng or random.SystemRandom()
        self.models = {name: model for model in config.prank.models for name in [model.id, *model.aliases]}
        if any(item.text is None for pool in config.prank.content_pools.values() for item in pool.items):
            raise ValueError("content files must be loaded with load_config before creating the application")
        if any(thought.text is None for pool in config.prank.content_pools.values() for item in pool.items
               if item.thinking for thought in item.thinking.items):
            raise ValueError("thinking files must be loaded with load_config before creating the application")
        for model in config.prank.models:
            for name in [model.id, *model.aliases]:
                encoded = claude_id(name)
                if encoded in self.models and self.models[encoded] is not model:
                    raise ValueError(f"Anthropic cloaked model ID collision: {encoded}")

    def resolve(self, name: str) -> Model:
        if not isinstance(name, str) or not name.strip():
            raise APIError("model is required")
        name = name.strip()
        if name in self.models:
            return self.models[name]
        # CPA's model(effort) suffix is accepted independently of response content.
        base = name.rsplit("(", 1)[0] if "(" in name and name.endswith(")") else name
        if base.startswith(CLAUDE_PREFIX):
            base = base[len(CLAUDE_PREFIX):][::-1]
        if base in self.models:
            return self.models[base]
        raise APIError(f"unknown provider for model {name}", 502, "server_error")

    def generate(self, payload: dict, *, prewarm: bool = False, allow_thinking: bool = True) -> Generation:
        name = payload.get("model")
        model = self.resolve(name)
        level = requested_effort(payload)
        settings = ResolvedStreaming.model_validate({**self.config.prank.streaming.model_dump(),
                                            **model.streaming.model_dump(exclude_none=True)})
        pool = self.config.prank.content_pools[model.content_pool or self.config.prank.default_content_pool]
        selected = None if prewarm else self.rng.choices(pool.items, weights=[item.weight for item in pool.items], k=1)[0]
        text = selected.text if selected else ""
        thought = ""
        if selected and selected.thinking and allow_thinking and level not in (None, "none"):
            items = selected.thinking.items
            thought = self.rng.choices(items, weights=[item.weight for item in items], k=1)[0].text
        if text is None:
            raise ValueError("content files must be loaded before creating the application")
        return Generation(name.strip(), text, settings, input_tokens(payload), estimate_tokens(text),
                          uuid4().hex, "msg_" + uuid4().hex, int(time.time()), thought, level)
