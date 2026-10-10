"""Unmodified upstream UI with an intentionally disabled management backend."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse


router = APIRouter()
STATIC = Path(__file__).resolve().parents[1] / "static"
MANAGEMENT_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]


@router.api_route("/management.html", methods=["GET", "HEAD"])
async def management_page():
    return FileResponse(STATIC / "management.html", media_type="text/html",
                        headers={"Cache-Control": "no-cache"})


@router.get("/management.LICENSE")
async def management_license():
    return FileResponse(STATIC / "management.LICENSE", media_type="text/plain")


@router.api_route("/v8/management", methods=MANAGEMENT_METHODS)
@router.api_route("/v8/management/{path:path}", methods=MANAGEMENT_METHODS)
@router.api_route("/v0/management", methods=MANAGEMENT_METHODS)
@router.api_route("/v0/management/{path:path}", methods=MANAGEMENT_METHODS)
async def management_disabled():
    # Do not inspect credentials or bodies, proxy requests, or save input.
    # A 403 prevents the upstream UI's 404-only legacy-backend probe.
    return JSONResponse({"error": "remote management is disabled"}, status_code=403,
                        headers={"Cache-Control": "no-store"})
