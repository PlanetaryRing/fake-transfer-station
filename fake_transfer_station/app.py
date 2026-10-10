import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.exceptions import HTTPException

from .auth import CPAMiddleware
from .config import Config, load_config
from .content import ContentEngine
from .errors import APIError, error_response
from .models import catalog, gemini_model
from .routes.generation import router as generation_router
from .routes.management import router as management_router
from .routes.websocket import router as websocket_router


def create_app(config: Config | None = None, *, config_path: str | Path | None = None) -> FastAPI:
    if config is None:
        path = config_path or os.environ.get("PRANK_CONFIG")
        if path is None:
            path = "config.yaml" if Path("config.yaml").exists() else "config.example.yaml"
        config = load_config(path)
    app = FastAPI(title="CLI Proxy API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config
    app.state.engine = ContentEngine(config)
    app.add_middleware(CPAMiddleware, keys=config.access.api_keys)
    app.add_exception_handler(APIError, error_response)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 404:
            return Response("404 page not found", status_code=404, media_type="text/plain")
        return await error_response(request, APIError(str(exc.detail), exc.status_code))

    @app.get("/")
    async def root():
        return {"message": "CLI Proxy API Server", "endpoints": [
            "POST /v1/chat/completions", "POST /v1/completions", "GET /v1/models"]}

    @app.api_route("/healthz", methods=["GET", "HEAD"])
    async def health(request: Request):
        return Response(status_code=200) if request.method == "HEAD" else {"status": "ok"}

    @app.get("/v1/models")
    async def list_models(request: Request):
        return catalog(config, request.headers, request.query_params)

    @app.get("/v1/models/{model:path}")
    async def model_detail(model: str, request: Request):
        return catalog(config, request.headers, request.query_params, model)

    @app.get("/v1beta/models")
    async def gemini_models():
        return {"models": [gemini_model(model) for model in config.prank.models]}

    @app.get("/v1beta/models/{model:path}")
    async def gemini_detail(model: str):
        for item in config.prank.models:
            if item.id.removeprefix("models/") == model:
                return gemini_model(item)
        raise APIError("Not Found", 404, "not_found")

    app.include_router(generation_router)
    app.include_router(management_router)
    app.include_router(websocket_router)
    return app
