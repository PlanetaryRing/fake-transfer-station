"""Select a pool using only the last user message, never system/tool history."""

import re

from .config import Model, TextItem


def text_blocks(value, *, gemini: bool = False) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    texts = []
    for block in value:
        if not isinstance(block, dict):
            continue
        if not gemini and block.get("type") not in ("text", "input_text"):
            continue
        if gemini and block.get("thought"):
            continue
        text = block.get("text")
        if isinstance(text, str):
            texts.append(text)
    return "\n".join(texts)


def last_user_text(payload: dict) -> str:
    if "messages" in payload:
        messages, gemini = payload["messages"], False
    elif "input" in payload:
        if isinstance(payload["input"], str):
            return payload["input"]
        messages, gemini = payload["input"], False
    elif "contents" in payload:
        messages, gemini = payload["contents"], True
    else:
        prompt = payload.get("prompt")
        return prompt if isinstance(prompt, str) else ""
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        # Gemini permits omitted role for a user Content object.
        if message.get("role", "user" if gemini else None) != "user":
            continue
        if not gemini and message.get("type", "message") != "message":
            continue
        # Do not fall back to an earlier message if the latest has no text.
        return text_blocks(message.get("parts" if gemini else "content"), gemini=gemini)
    return ""


def model_identity(model: Model) -> str:
    if model.identity is not None:
        return model.identity
    name = model.id.lower()
    for prefix, identity in (("gpt-", "ChatGPT"), ("claude-", "Claude"), ("gemini-", "Gemini")):
        if name.startswith(prefix):
            return identity
    return "AI 助手"


def render_content(item: TextItem, model: Model) -> str:
    if item.template is not None:
        # Single pass, literal substitution. No expressions, format(), or eval().
        return re.sub(r"\{\{\s*identity\s*\}\}", lambda _: model_identity(model), item.template)
    if item.text is None:
        raise ValueError("content files must be loaded before creating the application")
    return item.text
