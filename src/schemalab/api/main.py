"""FastAPI application entry point."""

import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, StringConstraints
from sqlalchemy import Engine

from schemalab import __version__, store
from schemalab.agent.graph import run_agent
from schemalab.api.auth import UserDep, get_engine
from schemalab.api.auth import router as auth_router
from schemalab.api.corrections import router as corrections_router
from schemalab.config import Settings, get_settings
from schemalab.llm import Gateway, NoModelsError, gateway_from_settings

logger = logging.getLogger(__name__)


class AskRequest(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]


def get_db_path() -> Path:
    return Path(get_settings().db_path)


def get_gateway(settings: Annotated[Settings, Depends(get_settings)]) -> Gateway:
    try:
        return gateway_from_settings(settings)
    except NoModelsError as e:
        # The details name config and env vars; they belong in the server log only.
        logger.error("no usable model: %s", e)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "No language model is available right now."
        ) from None


def _sse(
    events: Iterator[dict[str, Any]], on_done: Callable[[dict[str, Any]], None]
) -> Iterator[str]:
    """Send each event; once the stream ends, hand the final event to `on_done`."""
    last: dict[str, Any] | None = None
    for event in events:
        last = event
        yield f"data: {json.dumps(event, default=str)}\n\n"
    if last is not None:
        on_done(last)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Schemalab API", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    app.include_router(auth_router)
    app.include_router(corrections_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/ask")
    def ask_endpoint(
        user: UserDep,  # first, so no other dependency runs for an anonymous request
        body: AskRequest,
        db_path: Annotated[Path, Depends(get_db_path)],
        gateway: Annotated[Gateway, Depends(get_gateway)],
        settings: Annotated[Settings, Depends(get_settings)],
        engine: Annotated[Engine, Depends(get_engine)],
    ) -> StreamingResponse:
        with engine.connect() as conn:
            notes = [c["text"] for c in store.list_corrections(conn, user["id"])]

        def save(outcome: dict[str, Any]) -> None:
            with engine.begin() as conn:
                store.add_history(conn, user["id"], body.question, outcome)

        # A sync generator: Starlette iterates it in a worker thread, so slow model calls do
        # not block the event loop.
        return StreamingResponse(
            _sse(
                run_agent(
                    body.question,
                    db_path,
                    gateway,
                    share_results=settings.share_results_with_model,
                    notes=notes,
                ),
                on_done=save,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/history")
    def history(
        user: UserDep, engine: Annotated[Engine, Depends(get_engine)]
    ) -> list[dict[str, Any]]:
        with engine.connect() as conn:
            return store.list_history(conn, user["id"])

    return app


__all__ = ["app", "create_app", "get_db_path", "get_engine", "get_gateway", "get_settings"]

app = create_app()
