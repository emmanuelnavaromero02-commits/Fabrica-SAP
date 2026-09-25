from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from fabrica.api import clients, documents, inbox, meta, projects, requirements, tracking
from fabrica.api.deps import build_verifier
from fabrica.config import get_settings
from fabrica.db.session import init_db
from fabrica.pipeline.runner import build_runner


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    app.state.runner = build_runner()
    app.state.oidc = build_verifier()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Fábrica SAP", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(get_settings().cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(inbox.router)
    app.include_router(clients.router)
    app.include_router(projects.router)
    app.include_router(documents.router)
    app.include_router(requirements.router)
    app.include_router(meta.router)
    app.include_router(tracking.router)

    @app.get("/health", tags=["sistema"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    web_dir = Path(get_settings().web_dir)
    if get_settings().web_dir and (web_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app


app = create_app()


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("fabrica.api.app:app", host="0.0.0.0", port=8000)
