"""SEO agent backend — FastAPI application entry point.

Extracted from the AgentOS backend (`backend/app/main.py`). Same middleware,
same error handlers, same `/api` prefix — only the router list is cut down to
what the SEO agent needs: `seo_geo` itself, plus `auth` (the frontend cannot
obtain a token without it) and `health`.

Dropped along with the other agents: the MR datastore exception handler and the
Graphics Designer dynamic-brand registration, neither of which has anything to
bind to here.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import auth, health, seo_geo

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="SEO Agent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _cors_headers(request: Request) -> dict[str, str]:
    """Echo the allowed Origin so the browser can read the real error body.

    Error responses are produced in Starlette's outer error middleware, outside
    CORSMiddleware — without this the frontend sees "Failed to fetch" instead of
    the message."""
    headers: dict[str, str] = {}
    origin = request.headers.get("origin")
    if origin and origin in settings.cors_origin_list:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
        headers["Vary"] = "Origin"
    return headers


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a 500 that still carries CORS headers.

    The catch-all handler runs in Starlette's outer error middleware (outside
    CORSMiddleware), so without this the browser sees a CORS failure ("Failed to
    fetch") instead of the actual error. We echo the allowed Origin so the
    frontend can read the real message, and include the detail to aid debugging.
    """
    logging.getLogger("agentos").exception("unhandled error: %s", exc)
    headers = _cors_headers(request)
    # Exception text can carry file paths / doc ids / model names — keep it in
    # the server log only outside local dev.
    detail = (
        f"Internal server error: {exc}"
        if settings.app_env == "development"
        else "Internal server error"
    )
    return JSONResponse(status_code=500, content={"detail": detail}, headers=headers)


for router in (health, auth, seo_geo):
    app.include_router(router.router, prefix="/api")


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "SEO Agent API", "docs": "/docs", "health": "/api/health"}
