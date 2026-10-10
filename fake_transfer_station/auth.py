"""CPA's config-access candidate order and permissive multi-header matching."""

import hmac

from starlette.datastructures import Headers, QueryParams
from starlette.responses import JSONResponse, Response


def authentication_error(headers: Headers, query: QueryParams, keys: list[str]) -> str | None:
    if not keys:
        return None
    authorization = headers.get("authorization", "")
    raw = [authorization, headers.get("x-goog-api-key", ""), headers.get("x-api-key", ""),
           query.get("key", ""), query.get("auth_token", "")]
    if not any(raw):
        return "Missing API key"
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        raw[0] = parts[1].strip()
    for candidate in raw:
        if candidate and any(hmac.compare_digest(candidate.encode(), key.encode()) for key in keys):
            return None
    return "Invalid API key"


class CPAMiddleware:
    """Pure ASGI middleware: never buffers or consumes streaming response bodies."""

    def __init__(self, app, keys: list[str]):
        self.app = app
        self.keys = keys

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_cors(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"access-control-allow-origin", b"*"),
                    (b"access-control-allow-methods", b"GET, POST, PUT, PATCH, DELETE, OPTIONS"),
                    (b"access-control-allow-headers", b"*"),
                    (b"access-control-expose-headers", b"X-Request-Id, Request-Id, Retry-After"),
                ]
            await send(message)

        if scope["method"] == "OPTIONS":
            await Response(status_code=204)(scope, receive, send_cors)
            return
        protected = any(scope["path"] == prefix or scope["path"].startswith(prefix + "/")
                        for prefix in ("/v1", "/v1beta", "/backend-api/codex"))
        if protected:
            error = authentication_error(Headers(scope=scope), QueryParams(scope["query_string"]), self.keys)
            if error:
                await JSONResponse({"error": error}, status_code=401)(scope, receive, send_cors)
                return
        await self.app(scope, receive, send_cors)
