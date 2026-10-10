import json

from fastapi import Request

from .errors import APIError


def reject_constant(value):
    raise ValueError(f"non-finite JSON number: {value}")


def parse_json(raw: bytes | bytearray | str) -> dict:
    try:
        payload = json.loads(raw, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise APIError("Invalid request: expected valid JSON") from exc
    if not isinstance(payload, dict):
        raise APIError("Invalid request: expected a JSON object")
    # json.loads accepts escaped unpaired surrogates and overflowing exponents.
    # Reject these before headers are sent or UTF-8/token accounting takes place.
    try:
        json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise APIError("Invalid request: JSON must contain finite numbers and valid Unicode") from exc
    if "stream" in payload and not isinstance(payload["stream"], bool):
        raise APIError("stream must be a boolean")
    return payload


async def read_payload(request: Request) -> dict:
    limit = request.app.state.config.prank.max_request_bytes
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > limit:
            raise APIError("Request body too large", 413)
    return parse_json(raw)


def validate_input(payload: dict, protocol: str) -> None:
    """Validate envelope types, leaving tools and provider extensions opaque."""
    if protocol == "chat":
        if "messages" not in payload and ("input" in payload or "instructions" in payload):
            validate_input(payload, "responses")
            return
        field, types = "messages", (list,)
    elif protocol == "messages":
        field, types = "messages", (list,)
    elif protocol == "responses":
        field, types = "input", (str, list)
        if field not in payload and ("instructions" in payload or payload.get("previous_response_id")):
            return
    elif protocol == "gemini":
        field, types = "contents", (list,)
    else:
        field, types = "prompt", (str, list)
    if field not in payload or not isinstance(payload[field], types):
        raise APIError(f"Invalid request: {field} has a missing or invalid value")
    if protocol in ("chat", "messages", "gemini") and isinstance(payload[field], list):
        if not all(isinstance(item, dict) for item in payload[field]):
            raise APIError(f"Invalid request: {field} entries must be objects")
