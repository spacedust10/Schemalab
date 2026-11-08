"""FastAPI application entry point."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, StringConstraints

from schemalab import __version__
from schemalab.agent.ask import ask
from schemalab.config import get_settings
from schemalab.llm import Gateway, gateway_from_settings


class AskRequest(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]


def get_db_path() -> Path:
    return Path(get_settings().db_path)


def get_gateway() -> Gateway:
    return gateway_from_settings(get_settings())


def _sse(events: Iterator[dict[str, object]]) -> Iterator[str]:
    for event in events:
        yield f"data: {json.dumps(event, default=str)}\n\n"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Schemalab API", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/ask")
    def ask_endpoint(
        body: AskRequest,
        db_path: Annotated[Path, Depends(get_db_path)],
        gateway: Annotated[Gateway, Depends(get_gateway)],
    ) -> StreamingResponse:
        # A sync generator: Starlette iterates it in a worker thread, so slow model calls do
        # not block the event loop.
        return StreamingResponse(
            _sse(ask(body.question, db_path, gateway)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
