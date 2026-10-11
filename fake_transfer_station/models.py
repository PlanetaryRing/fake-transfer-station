from datetime import datetime, timezone

from .config import Config, Model
from .content import claude_id


def openai_model(model: Model) -> dict:
    return {"id": model.id, "object": "model", "created": model.created, "owned_by": model.owned_by}


def anthropic_model(model: Model, config: Config) -> dict:
    return {"id": model.id if config.upstream.claude.disable_cloaking_model_list else claude_id(model.id),
            "type": "model", "display_name": model.display_name or model.id,
            "created_at": datetime.fromtimestamp(model.created, timezone.utc).isoformat().replace("+00:00", "Z"),
            "max_input_tokens": model.context_length, "max_tokens": model.max_output_tokens}


def gemini_model(model: Model) -> dict:
    return {"name": "models/" + model.id.removeprefix("models/"), "version": "1",
            "displayName": model.display_name or model.id, "description": model.display_name or model.id,
            "inputTokenLimit": model.context_length, "outputTokenLimit": model.max_output_tokens,
            "supportedGenerationMethods": ["generateContent", "streamGenerateContent", "countTokens"]}


def codex_model(model: Model, priority: int, client_version: str) -> dict:
    instructions = "You are Codex, a coding agent. You and the user share one workspace."
    entry = {
        "slug": model.id, "display_name": model.display_name or model.id,
        "description": model.display_name or model.id, "visibility": "list", "priority": priority,
        "supported_in_api": True, "context_window": model.context_length,
        "max_context_window": model.context_length, "max_tokens": model.max_output_tokens,
        "effective_context_window_percent": 95, "auto_compact_token_limit": int(model.context_length * .9),
        "default_reasoning_level": "none",
        "supported_reasoning_levels": [{"effort": level, "description": level.title()} for level in ("none", "low", "medium", "high")],
        "supports_reasoning_summaries": True, "support_verbosity": False,
        "default_verbosity": None, "default_reasoning_summary": "auto",
        "shell_type": "shell_command", "apply_patch_tool_type": None,
        "upgrade": None, "availability_nux": None, "prefer_websockets": False,
        "supports_search_tool": False, "supports_parallel_tool_calls": False,
        "supports_image_detail_original": False, "input_modalities": ["text"],
        "service_tiers": [], "additional_tools": [], "experimental_supported_tools": [],
        "available_in_plans": [], "base_instructions": instructions,
        "model_messages": {"instructions_template": instructions, "instructions_variables": None,
                           "approvals": None, "collaboration_modes": None, "auto_review": None,
                           "permissions": None, "multi_agent": None},
        "truncation_policy": {"mode": "bytes", "limit": 10000},
    }
    if client_version == "cpa":
        entry["cpa_capabilities"] = {"web_search": False}
    return entry


def catalog(config: Config, headers, query, detail: str | None = None) -> dict:
    models = config.prank.models
    if "client_version" in query:
        entries = [codex_model(model, (i + 1) * 100, query["client_version"]) for i, model in enumerate(models)]
        key, id_key = "models", "slug"
        result = {key: entries}
    elif headers.get("anthropic-version") or headers.get("user-agent", "").startswith("claude-cli"):
        entries = sorted([anthropic_model(model, config) for model in models], key=lambda item: (item["display_name"], item["id"]))
        key, id_key = "data", "id"
        result = {key: entries, "has_more": False, "first_id": entries[0]["id"], "last_id": entries[-1]["id"]}
    else:
        entries = [openai_model(model) for model in models]
        key, id_key = "data", "id"
        result = {"object": "list", key: entries}
    if detail is not None:
        for entry in entries:
            if entry[id_key] == detail:
                return entry
        from .errors import APIError
        raise APIError("Not Found", 404, "not_found")
    return result
