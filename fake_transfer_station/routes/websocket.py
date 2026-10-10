"""Connection-local Responses turns; no account or conversation persistence."""

import asyncio
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from ..auth import authentication_error
from ..errors import APIError
from ..protocols import response_events
from ..requests import parse_json, validate_input

router = APIRouter()


@router.websocket("/v1/responses")
@router.websocket("/backend-api/codex/responses")
async def websocket_responses(socket: WebSocket):
    config = socket.app.state.config
    error = authentication_error(socket.headers, socket.query_params, config.access.api_keys)
    if error:
        await socket.send_denial_response(JSONResponse({"error": error}, status_code=401))
        return
    headers = []
    if turn_state := socket.headers.get("x-codex-turn-state"):
        headers.append((b"x-codex-turn-state", turn_state.encode("latin-1")))
    await socket.accept(headers=headers)
    previous_id = None
    previous_model = None
    active = None
    send_lock = asyncio.Lock()

    async def send(event):
        async with send_lock:
            await socket.send_json(event)

    async def send_error(exc: APIError):
        await send({"type": "error", "status": exc.status, "error": exc.detail()})

    async def produce(payload, generation, prewarm):
        nonlocal previous_id, previous_model
        async for event in response_events(generation, prewarm=prewarm):
            if event["type"] == "response.completed":
                previous_id = event["response"]["id"]
                previous_model = payload["model"]
            await send(event)

    receive = asyncio.create_task(socket.receive())
    try:
        while True:
            pending = {receive}
            if active is not None:
                pending.add(active)
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if active is not None and active in done:
                await active
                active = None
            if receive not in done:
                continue
            message = receive.result()
            if message["type"] == "websocket.disconnect":
                return
            raw = message.get("text")
            if raw is None:
                raw = message.get("bytes", b"")
            receive = asyncio.create_task(socket.receive())
            try:
                size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
                if size > config.prank.max_request_bytes:
                    await socket.close(code=1009, reason="message too big")
                    return
                payload = parse_json(raw)
                kind = payload.get("type")
                if kind == "response.interrupt":
                    # Stop at once, then allow a new full request on this socket.
                    # No invented upstream steering/acknowledgement events.
                    if active is not None:
                        active.cancel()
                        with suppress(asyncio.CancelledError):
                            await active
                        active = None
                    await send_error(APIError("Response interrupted", 409, code="response_interrupted"))
                    continue
                if kind not in ("response.create", "response.append"):
                    raise APIError("Expected response.create or response.append")
                if active is not None:
                    raise APIError("A response is already in progress", 409, code="response_in_progress")
                parent = payload.get("previous_response_id")
                if (parent and parent != previous_id) or (kind == "response.append" and previous_id is None):
                    raise APIError("Previous response is not available on this websocket; resend the full conversation input without previous_response_id",
                                   409, code="previous_response_not_found")
                if "model" not in payload and previous_model:
                    payload["model"] = previous_model
                payload.setdefault("input", [])
                if not isinstance(payload["input"], list):
                    raise APIError("websocket request requires array field: input")
                validate_input(payload, "responses")
                prewarm = payload.get("generate") is False
                generation = socket.app.state.engine.generate(payload, prewarm=prewarm)
                active = asyncio.create_task(produce(payload, generation, prewarm))
            except APIError as exc:
                await send_error(exc)
    except (WebSocketDisconnect, OSError):
        pass
    finally:
        tasks = [task for task in (receive, active) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
