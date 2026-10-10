"""Validated startup configuration. Content files are loaded once per process."""

import math
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda name: name.replace("_", "-"),
        populate_by_name=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class Server(Settings):
    host: str = "0.0.0.0"
    port: int = Field(default=8317, ge=1, le=65535)


class Access(Settings):
    api_keys: list[str] = Field(default_factory=list)

    @field_validator("api_keys")
    @classmethod
    def normalize_keys(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(key.strip() for key in values if key.strip()))


class Streaming(Settings):
    initial_delay_ms: float = Field(default=300, ge=0)
    chunk_size: int = Field(default=4, ge=1)
    keepalive_seconds: float = Field(default=15, ge=0)


class StreamingOverride(Settings):
    initial_delay_ms: float | None = Field(default=None, ge=0)
    chunk_size: int | None = Field(default=None, ge=1)
    keepalive_seconds: float | None = Field(default=None, ge=0)


class Speed(Settings):
    tokens_per_second: float = Field(ge=0)
    tokens_per_second_offset: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        rate, offset = self.tokens_per_second, self.tokens_per_second_offset
        if rate == 0 and offset != 0:
            raise ValueError("unlimited speed (tokens-per-second=0) requires offset=0")
        if rate > 0 and offset >= rate:
            raise ValueError("tokens-per-second-offset must be less than tokens-per-second")
        if not math.isfinite(rate + offset):
            raise ValueError("maximum token rate must be finite")
        return self


class ModelStreaming(Speed, StreamingOverride):
    """Required per-model rate, with optional overrides for shared framing."""


class ResolvedStreaming(Speed, Streaming):
    """A request-local snapshot with all framing settings resolved."""


class ContentItem(Settings):
    id: str | None = None
    text: str | None = None
    file: str | None = None
    weight: float = Field(default=1, gt=0)

    @model_validator(mode="after")
    def exactly_one_source(self) -> Self:
        if (self.text is None) == (self.file is None):
            raise ValueError("specify exactly one of text or file")
        if self.text == "" or self.file == "":
            raise ValueError("content source must not be empty")
        return self


class ContentPool(Settings):
    selection: Literal["random"] = "random"
    items: list[ContentItem] = Field(min_length=1)


class Model(Settings):
    id: str = Field(min_length=1)
    display_name: str = ""
    owned_by: str = "openai"
    created: int = Field(default=1700000000, ge=0, le=253402300799)
    context_length: int = Field(default=128000, gt=0)
    max_output_tokens: int = Field(default=16384, gt=0)
    aliases: list[str] = Field(default_factory=list)
    content_pool: str | None = None
    streaming: ModelStreaming

    @field_validator("id")
    @classmethod
    def clean_id(cls, value: str) -> str:
        if value != value.strip() or not value:
            raise ValueError("model id must be nonempty and have no surrounding whitespace")
        return value


class Prank(Settings):
    streaming: Streaming = Field(default_factory=Streaming)
    default_content_pool: str = "funny"
    models: list[Model] = Field(min_length=1)
    content_pools: dict[str, ContentPool]
    max_request_bytes: int = Field(default=8 * 1024 * 1024, gt=0)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        if self.default_content_pool not in self.content_pools:
            raise ValueError(f"unknown default content pool: {self.default_content_pool}")
        names: set[str] = set()
        for model in self.models:
            for name in [model.id, *model.aliases]:
                if not name or name != name.strip() or name in names:
                    raise ValueError(f"duplicate or invalid model id/alias: {name!r}")
                names.add(name)
            if model.content_pool is not None and model.content_pool not in self.content_pools:
                raise ValueError(f"model {model.id}: unknown content pool {model.content_pool}")
        return self


class ClaudeOptions(Settings):
    disable_cloaking_model_list: bool = False


class Upstream(Settings):
    claude: ClaudeOptions = Field(default_factory=ClaudeOptions)


class Config(Settings):
    config_version: Literal[8] = 8
    server: Server = Field(default_factory=Server)
    access: Access = Field(default_factory=Access)
    upstream: Upstream = Field(default_factory=Upstream)
    prank: Prank


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate keys rather than silently hiding a configuration typo."""


def unique_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode) -> dict:
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise ValueError(f"configuration keys must be strings at {key_node.start_mark}")
        if key in result:
            raise ValueError(f"duplicate YAML key {key!r} at {key_node.start_mark}")
        result[key] = loader.construct_object(value_node)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    raw = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=UniqueKeyLoader)
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a YAML mapping")
    for section in ("server", "access"):
        if section in raw and not isinstance(raw[section], dict):
            raise ValueError(f"{section} must be a YAML mapping")
    # CPA legacy spellings; explicit v8 settings win, even empty lists/strings.
    for key in ("host", "port"):
        if key in raw:
            raw.setdefault("server", {}).setdefault(key, raw.pop(key))
    if "api-keys" in raw:
        raw.setdefault("access", {}).setdefault("api-keys", raw.pop("api-keys"))
    config = Config.model_validate(raw)
    for pool_name, pool in config.prank.content_pools.items():
        for index, item in enumerate(pool.items):
            if item.file is not None:
                source = path.parent / item.file
                try:
                    # newline="" preserves the artwork exactly, including CRLF.
                    with source.open(encoding="utf-8-sig", newline="") as handle:
                        text = handle.read()
                except (OSError, UnicodeError) as exc:
                    raise ValueError(f"content pool {pool_name}, item {index}: {exc}") from exc
                if not text:
                    raise ValueError(f"content pool {pool_name}, item {index}: empty file {source}")
                item.text = text
                item.file = None
    return config
