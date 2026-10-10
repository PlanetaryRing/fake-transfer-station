from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..content import input_tokens
from ..errors import APIError
from ..protocols import (
    anthropic_frames, anthropic_message, chat_completion, chat_frames,
    gemini_frames, gemini_response, response_frames, response_object,
)
from ..requests import read_payload, validate_input
from ..streaming import sse_response

router = APIRouter()


def complete_json(body, generation):
    return JSONResponse(body, headers={"X-Request-Id": "req_" + generation.id, "Request-Id": "req_" + generation.id})


async def chat(request: Request, legacy: bool):
    payload = await read_payload(request)
    validate_input(payload, "completions" if legacy else "chat")
    if payload.get("n", 1) != 1:
        raise APIError("This endpoint supports n=1 only")
    generation = request.app.state.engine.generate(payload)
    options = payload.get("stream_options")
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise APIError("stream_options must be an object")
    if payload.get("stream", False):
        return sse_response(chat_frames(generation, legacy=legacy, include_usage=options.get("include_usage") is True), generation)
    return complete_json(chat_completion(generation, legacy=legacy), generation)


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    return await chat(request, False)


@router.post("/v1/completions")
async def completions(request: Request):
    return await chat(request, True)


@router.post("/v1/messages")
async def messages(request: Request):
    payload = await read_payload(request)
    validate_input(payload, "messages")
    generation = request.app.state.engine.generate(payload)
    if payload.get("stream", False):
        return sse_response(anthropic_frames(generation), generation)
    return complete_json(anthropic_message(generation), generation)


@router.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    payload = await read_payload(request)
    validate_input(payload, "messages")
    request.app.state.engine.resolve(payload.get("model"))
    return {"input_tokens": input_tokens(payload)}


@router.post("/v1/responses")
@router.post("/backend-api/codex/responses")
async def responses(request: Request):
    payload = await read_payload(request)
    validate_input(payload, "responses")
    generation = request.app.state.engine.generate(payload)
    if payload.get("stream", False):
        return sse_response(response_frames(generation), generation)
    return complete_json(response_object(generation), generation)


@router.post("/v1/responses/compact")
@router.post("/backend-api/codex/responses/compact")
async def compact(request: Request):
    payload = await read_payload(request)
    if payload.get("stream", False):
        raise APIError("Streaming not supported for compact responses")
    validate_input(payload, "responses")
    generation = request.app.state.engine.generate(payload, prewarm=True)
    source = payload.get("input", [])
    if isinstance(source, str):
        source = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": source}]}]
    users = [item for item in source if isinstance(item, dict) and item.get("role") == "user"]
    # Opaque mock marker, not encrypted upstream data. Generation ignores history.
    marker = {"id": "cmp_" + uuid4().hex, "type": "compaction", "encrypted_content": "prank_" + uuid4().hex}
    return complete_json({"id": "resp_" + generation.id, "object": "response.compaction",
                          "created_at": generation.created, "output": [*users, marker],
                          "usage": generation.response_usage()}, generation)


@router.post("/v1beta/models/{action:path}")
async def gemini_generate(action: str, request: Request):
    if ":" not in action:
        raise APIError("Not Found", 404, "not_found")
    name, method = action.rsplit(":", 1)
    if method not in ("generateContent", "streamGenerateContent", "countTokens"):
        raise APIError("Not Found", 404, "not_found")
    payload = await read_payload(request)
    payload["model"] = name
    validate_input(payload, "gemini")
    if method == "countTokens":
        request.app.state.engine.resolve(name)
        return {"totalTokens": input_tokens(payload)}
    generation = request.app.state.engine.generate(payload)
    if method == "streamGenerateContent":
        return sse_response(gemini_frames(generation), generation)
    return complete_json(gemini_response(generation), generation)
