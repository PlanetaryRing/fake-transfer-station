"""Protocol adapters for a single immutable, randomly selected generation."""

import time

from .content import Generation
from .streaming import encode_sse


def chat_completion(g: Generation, *, legacy: bool = False) -> dict:
    choice = {"index": 0, "finish_reason": "stop", "logprobs": None}
    if legacy:
        choice["text"] = g.text
    else:
        choice["message"] = {"role": "assistant", "content": g.text, "refusal": None}
    return {"id": ("cmpl-" if legacy else "chatcmpl-") + g.id,
            "object": "text_completion" if legacy else "chat.completion",
            "created": g.created, "model": g.model, "choices": [choice], "usage": g.chat_usage()}


async def chat_frames(g: Generation, *, legacy: bool = False, include_usage: bool = False):
    base = {"id": ("cmpl-" if legacy else "chatcmpl-") + g.id,
            "object": "text_completion" if legacy else "chat.completion.chunk",
            "created": g.created, "model": g.model}

    def chunk(delta, finish=None):
        choice = {"index": 0, "finish_reason": finish, "logprobs": None}
        choice["text" if legacy else "delta"] = delta
        return {**base, "choices": [choice], **({"usage": None} if include_usage else {})}

    if not legacy:
        yield encode_sse(chunk({"role": "assistant", "content": ""}))
    async for text in g.chunks():
        yield encode_sse(chunk(text if legacy else {"content": text}))
    yield encode_sse(chunk("" if legacy else {}, "stop"))
    if include_usage:
        yield encode_sse({**base, "choices": [], "usage": g.chat_usage()})
    yield encode_sse("[DONE]")


def anthropic_message(g: Generation, *, start: bool = False) -> dict:
    return {"id": g.message_id, "type": "message", "role": "assistant", "model": g.model,
            "content": [] if start else [{"type": "text", "text": g.text}],
            "stop_reason": None if start else "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": g.input_tokens, "output_tokens": 0 if start else g.output_tokens,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


async def anthropic_frames(g: Generation):
    def event(kind, **fields):
        return encode_sse({"type": kind, **fields}, kind)

    yield event("message_start", message=anthropic_message(g, start=True))
    yield event("content_block_start", index=0, content_block={"type": "text", "text": ""})
    async for text in g.chunks():
        yield event("content_block_delta", index=0, delta={"type": "text_delta", "text": text})
    yield event("content_block_stop", index=0)
    yield event("message_delta", delta={"stop_reason": "end_turn", "stop_sequence": None},
                usage={"output_tokens": g.output_tokens})
    yield event("message_stop")


def output_text(text: str) -> dict:
    return {"type": "output_text", "text": text, "annotations": [], "logprobs": []}


def response_item(g: Generation, *, start: bool = False) -> dict:
    return {"id": g.message_id, "type": "message", "role": "assistant", "phase": "final_answer",
            "status": "in_progress" if start else "completed",
            "content": [] if start else [output_text(g.text)]}


def response_object(g: Generation, *, start: bool = False, prewarm: bool = False) -> dict:
    return {"id": "resp_" + g.id, "object": "response", "created_at": g.created,
            "completed_at": None if start else int(time.time()),
            "status": "in_progress" if start else "completed", "model": g.model,
            "error": None, "incomplete_details": None, "instructions": None,
            "output": [] if start or prewarm else [response_item(g)],
            "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
            "temperature": 1.0, "top_p": 1.0, "metadata": {}, "store": False,
            "text": {"format": {"type": "text"}}, "reasoning": {"effort": None, "summary": None},
            "usage": None if start else g.response_usage()}


async def response_events(g: Generation, *, prewarm: bool = False):
    sequence = 0

    def event(kind, **fields):
        nonlocal sequence
        result = {"type": kind, "sequence_number": sequence, **fields}
        sequence += 1
        return result

    yield event("response.created", response=response_object(g, start=True))
    yield event("response.in_progress", response=response_object(g, start=True))
    if not prewarm:
        yield event("response.output_item.added", output_index=0, item=response_item(g, start=True))
        location = {"item_id": g.message_id, "output_index": 0, "content_index": 0}
        yield event("response.content_part.added", **location, part=output_text(""))
        async for text in g.chunks():
            yield event("response.output_text.delta", **location, delta=text, logprobs=[])
        yield event("response.output_text.done", **location, text=g.text, logprobs=[])
        yield event("response.content_part.done", **location, part=output_text(g.text))
        yield event("response.output_item.done", output_index=0, item=response_item(g))
    yield event("response.completed", response=response_object(g, prewarm=prewarm))


async def response_frames(g: Generation):
    async for event in response_events(g):
        yield encode_sse(event, event["type"])


def gemini_response(g: Generation, text: str | None = None, *, final: bool = True) -> dict:
    candidate = {"index": 0, "content": {"role": "model", "parts": [{"text": g.text if text is None else text}]}}
    result = {"candidates": [candidate], "modelVersion": g.model, "responseId": "resp_" + g.id}
    if final:
        candidate["finishReason"] = "STOP"
        result["usageMetadata"] = {"promptTokenCount": g.input_tokens, "candidatesTokenCount": g.output_tokens,
                                   "totalTokenCount": g.input_tokens + g.output_tokens}
    return result


async def gemini_frames(g: Generation):
    async for text in g.chunks():
        yield encode_sse(gemini_response(g, text, final=False))
    yield encode_sse(gemini_response(g, "", final=True))
