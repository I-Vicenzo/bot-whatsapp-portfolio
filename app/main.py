"""Ponto de entrada da aplicação web.

Rodar: `uv run uvicorn app.main:create_app --factory --reload`.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.channels import simulator
from app.channels.whatsapp import router as whatsapp
from app.channels.whatsapp.client import WhatsAppClient
from app.clinic import load_clinic
from app.clock import Clock, SystemClock
from app.config import Settings, get_settings
from app.conversation import ConversationEngine
from app.conversation.service import ConversationService
from app.db.demo import seed_demo_appointments
from app.db.engine import make_engine, make_session_factory
from app.db.migrate import run_migrations
from app.db.sync import sync_clinic
from app.ratelimit import RateLimiter
from app.scheduling import Scheduler

STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)


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
        scheduler = Scheduler(clinic, clock)
        with session_factory.begin() as session:
            sync_clinic(session, clinic)
            if settings.demo_seed:
                created = seed_demo_appointments(session, clinic, scheduler)
                if created:
                    logger.info("Dados de demonstração: %d consultas criadas", created)

        conversation_engine = ConversationEngine(clinic, scheduler, clock)
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.scheduler = scheduler
        app.state.conversation_engine = conversation_engine
        app.state.conversation_service = ConversationService(
            session_factory, conversation_engine, clock
        )
        app.state.simulator_limiter = RateLimiter(
            settings.simulator_rate_limit_per_minute, window_seconds=60
        )
        app.state.whatsapp_client = _whatsapp_client(settings)
        try:
            yield
        finally:
            if app.state.whatsapp_client is not None:
                app.state.whatsapp_client.close()
            engine.dispose()

    app = FastAPI(
        title="Assistente virtual de clínica",
        version=__version__,
        description=(
            "Bot de atendimento no WhatsApp para uma clínica **fictícia**. "
            "O simulador web usa exatamente o mesmo motor de conversa do WhatsApp."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.clinic = clinic
    app.state.clock = clock

    @app.get("/health", tags=["status"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/status", tags=["status"], summary="Flags para a interface")
    def api_status() -> dict[str, object]:
        return {
            "version": __version__,
            "clinic_name": clinic.clinic.name,
            "demo_mode": settings.demo_mode,
            "whatsapp_enabled": settings.whatsapp_enabled,
            "ai_enabled": settings.ai_enabled,
            "fictitious_data": True,
        }

    app.include_router(simulator.router)
    app.include_router(whatsapp.router)

    @app.get("/", include_in_schema=False)
    def simulator_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "simulator" / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _whatsapp_client(settings: Settings) -> WhatsAppClient | None:
    if not settings.whatsapp_enabled:
        return None
    return WhatsAppClient(
        token=settings.whatsapp_token.get_secret_value(),  # type: ignore[union-attr]
        phone_number_id=settings.whatsapp_phone_number_id,  # type: ignore[arg-type]
        api_version=settings.whatsapp_api_version,
    )
