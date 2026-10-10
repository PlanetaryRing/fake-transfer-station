from fastapi import Request
from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(self, message: str, status: int = 400, kind: str = "invalid_request_error", code: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.kind = kind
        self.code = code

    def detail(self) -> dict:
        result = {"message": self.message, "type": self.kind}
        if self.code is not None:
            result["code"] = self.code
        return result


async def error_response(request: Request, exc: APIError) -> JSONResponse:
    body = {"error": exc.detail()}
    if request.url.path.startswith("/v1/messages"):
        body["type"] = "error"
    return JSONResponse(body, status_code=exc.status)
