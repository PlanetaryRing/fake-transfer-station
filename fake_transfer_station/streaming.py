"""SSE framing, heartbeats and disconnect-aware cancellation."""

import asyncio
import json
from contextlib import suppress

from starlette.responses import StreamingResponse


def encode_sse(data: dict | str, event: str | None = None) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return (f"event: {event}\n" if event else "") + f"data: {payload}\n\n"


async def with_heartbeats(source, interval: float):
    """Keep one pending next() alive; a heartbeat must not cancel a slow chunk."""
    iterator = source.__aiter__()
    pending = None
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(anext(iterator))
            done, _ = await asyncio.wait({pending}, timeout=interval if interval > 0 else None)
            if not done:
                yield ": keepalive\n\n"
                continue
            try:
                frame = pending.result()
            except StopAsyncIteration:
                break
            pending = None
            yield frame
    finally:
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        await iterator.aclose()


class CancellableStreamingResponse(StreamingResponse):
    """Cancel sleeping generators immediately on disconnect, on all ASGI versions."""

    async def __call__(self, scope, receive, send):
        stream = asyncio.create_task(self.stream_response(send))
        disconnected = asyncio.create_task(self.listen_for_disconnect(receive))
        try:
            done, _ = await asyncio.wait({stream, disconnected}, return_when=asyncio.FIRST_COMPLETED)
            if stream in done:
                await stream
        except OSError:
            # A disconnected socket can also be reported by send().
            pass
        finally:
            for task in (stream, disconnected):
                task.cancel()
            await asyncio.gather(stream, disconnected, return_exceptions=True)
            await self.body_iterator.aclose()
        if self.background:
            await self.background()


def sse_response(frames, generation):
    return CancellableStreamingResponse(
        with_heartbeats(frames, generation.settings.keepalive_seconds),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "X-Request-Id": "req_" + generation.id, "Request-Id": "req_" + generation.id},
    )
