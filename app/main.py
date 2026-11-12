"""Ponto de entrada da aplicação web.

Rodar: `uv run uvicorn app.main:create_app --factory --reload`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.clinic import load_clinic
from app.clock import Clock, SystemClock
from app.config import Settings, get_settings
from app.conversation import ConversationEngine
from app.db.engine import make_engine, make_session_factory
from app.db.migrate import run_migrations
from app.db.sync import sync_clinic
from app.scheduling import Scheduler


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Falha cedo: arquivo da clínica inválido impede a criação da aplicação.
    clinic = load_clinic(settings.clinic_data_path)
    clock = clock or SystemClock(clinic.clinic.tz)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url, clock)
        run_migrations(engine)
        session_factory = make_session_factory(engine)
        with session_factory.begin() as session:
            sync_clinic(session, clinic)

        scheduler = Scheduler(clinic, clock)
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.scheduler = scheduler
        app.state.conversation_engine = ConversationEngine(clinic, scheduler, clock)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Assistente virtual de clínica",
        version=__version__,
        description="Bot de atendimento no WhatsApp para uma clínica fictícia.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.clinic = clinic
    app.state.clock = clock

    @app.get("/health", tags=["status"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app
