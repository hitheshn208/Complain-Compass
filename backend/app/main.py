"""Complaint Compass FastAPI application."""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import analytics, batches, channels, complaints, issues, settings as settings_api
from app.core.config import Settings, get_settings
from app.core.lifespan import make_lifespan

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Complaint Compass API",
        version="1.0.0",
        description="Customer complaint analytics: ingestion, semantic clustering, deduplication and prioritization.",
        lifespan=make_lifespan(settings),
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"]
    )

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"code": exc.status_code, "message": exc.detail}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": {"code": 422, "message": "Validation error",
                                                                "details": jsonable(exc.errors())}})

    for r in (complaints.router, channels.router, batches.router, issues.router, analytics.router,
              settings_api.router):
        app.include_router(r)

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        c = app.state.container
        return {"status": "ok", "llm_available": c.classifier.available, "embedding_model": c.embedder.encoder.model_name,
                "vectors": c.store.count(), "transcriber": c.transcriber.describe()}

    return app


def jsonable(obj):
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(obj, custom_encoder={Exception: str})


app = create_app()
