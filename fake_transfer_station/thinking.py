"""Normalize explicit thinking controls. Missing controls mean disabled."""

from .errors import APIError

EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "auto"}


def effort(value, field: str) -> str:
    if not isinstance(value, str) or value.strip().lower() not in EFFORTS:
        raise APIError(f"Invalid {field}: unsupported reasoning effort")
    return value.strip().lower()


def object_field(payload: dict, name: str) -> dict:
    value = payload.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise APIError(f"{name} must be an object")
    return value


def requested_effort(payload: dict) -> str | None:
    """Body controls beat model suffixes; any explicit body disable wins.

    All supplied controls are validated even if another control disables thinking.
    """
    levels = []
    if "reasoning_effort" in payload:
        levels.append(effort(payload["reasoning_effort"], "reasoning_effort"))
    for name in ("reasoning", "output_config"):
        block = object_field(payload, name)
        if "effort" in block:
            levels.append(effort(block["effort"], name + ".effort"))
    thinking = object_field(payload, "thinking")
    if thinking:
        mode = thinking.get("type")
        if mode not in ("enabled", "adaptive", "disabled"):
            raise APIError("thinking.type must be enabled, adaptive or disabled")
        if "budget_tokens" in thinking:
            budget = thinking["budget_tokens"]
            if type(budget) is not int or budget <= 0:
                raise APIError("thinking.budget_tokens must be a positive integer")
        levels.append("none" if mode == "disabled" else "auto")

    generation = object_field(payload, "generationConfig")
    gemini = object_field(generation, "thinkingConfig")
    if "thinkingLevel" in gemini:
        levels.append(effort(gemini["thinkingLevel"], "thinkingLevel"))
    if "thinkingBudget" in gemini:
        budget = gemini["thinkingBudget"]
        if type(budget) is not int or budget < -1:
            raise APIError("thinkingBudget must be -1 or a nonnegative integer")
        levels.append("none" if budget == 0 else "auto")
    if "includeThoughts" in gemini:
        if type(gemini["includeThoughts"]) is not bool:
            raise APIError("includeThoughts must be a boolean")
        if not gemini["includeThoughts"]:
            levels.append("none")
        # includeThoughts=true alone requests visibility, not a thinking budget.

    if levels:
        if "none" in levels:
            return "none"
        return next((level for level in levels if level != "auto"), "auto")
    model = payload.get("model", "")
    if isinstance(model, str) and "(" in model and model.endswith(")"):
        return effort(model.rsplit("(", 1)[1][:-1], "model thinking suffix")
    return None
