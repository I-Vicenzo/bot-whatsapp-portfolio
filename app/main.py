"""Ponto de entrada da aplicação web.

Rodar: `uv run uvicorn app.main:create_app --factory --reload`.
"""

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.admin import router as admin
from app.admin.auth import SessionCodec, ensure_initial_user
from app.ai.client import OpenAICompatibleClient
from app.ai.responder import AILimits, AIResponder
from app.ai.transcription import Transcriber
from app.channels import simulator
from app.channels.whatsapp import router as whatsapp
from app.channels.whatsapp.client import WhatsAppClient, WhatsAppError
from app.channels.whatsapp.router import WhatsAppOptions
from app.clinic import load_clinic
from app.clock import Clock, SystemClock
from app.config import Settings, get_settings
from app.conversation import ConversationEngine
from app.conversation.service import ConversationService, DeliveryError, Sender
from app.db.demo import seed_demo_appointments, seed_demo_conversations
from app.db.engine import make_engine, make_session_factory
from app.db.migrate import run_migrations
from app.db.models import Channel
from app.db.sync import sync_clinic
from app.ratelimit import RateLimiter
from app.scheduling import Scheduler

STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    settings = settings or get_settings()
    problems = settings.production_problems()
    if problems:
        raise RuntimeError("Configuração insegura para produção: " + "; ".join(problems))
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
            ensure_initial_user(session, settings, clock)
            if settings.demo_seed:
                created = seed_demo_appointments(session, clinic, scheduler)
                if created:
                    logger.info("Dados de demonstração: %d consultas criadas", created)

        llm = _llm_client(settings)
        ai = AIResponder(llm, clinic, clock, _ai_limits(settings)) if llm else None
        conversation_engine = ConversationEngine(clinic, scheduler, clock, ai=ai)
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.scheduler = scheduler
        app.state.conversation_engine = conversation_engine
        whatsapp_client = _whatsapp_client(settings)
        transcriber = _transcriber(settings) if whatsapp_client else None
        app.state.whatsapp_options = WhatsAppOptions(
            interactive=settings.whatsapp_interactive, transcriber=transcriber
        )
        app.state.whatsapp_client = whatsapp_client
        senders: dict[Channel, Sender] = {}
        if whatsapp_client is not None:
            senders[Channel.WHATSAPP] = _whatsapp_sender(whatsapp_client)
        service = ConversationService(session_factory, conversation_engine, clock, senders)
        app.state.conversation_service = service
        if settings.demo_seed and seed_demo_conversations(service):
            logger.info("Dados de demonstração: conversas de exemplo criadas")
        app.state.simulator_limiter = RateLimiter(
            settings.simulator_rate_limit_per_minute, window_seconds=60
        )
        try:
            yield
        finally:
            if app.state.whatsapp_client is not None:
                app.state.whatsapp_client.close()
            if llm is not None:
                llm.close()
            if transcriber is not None:
                transcriber.close()
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
    app.state.session_codec = SessionCodec(_session_secret(settings))
    app.state.login_limiter = RateLimiter(5, window_seconds=60)
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
    app.include_router(admin.router)

    @app.get("/admin", include_in_schema=False)
    def admin_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "admin" / "index.html")

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


def _llm_client(settings: Settings) -> OpenAICompatibleClient | None:
    if not settings.ai_enabled:
        return None
    return OpenAICompatibleClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),  # type: ignore[union-attr]
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def _ai_limits(settings: Settings) -> AILimits:
    return AILimits(
        max_question_chars=settings.llm_max_question_chars,
        calls_per_contact_per_hour=settings.llm_calls_per_contact_per_hour,
        daily_cap=settings.llm_daily_cap,
    )


def _session_secret(settings: Settings) -> str:
    if settings.session_secret is not None:
        return settings.session_secret.get_secret_value()
    logger.warning(
        "SESSION_SECRET não definido: gerado na inicialização (logins caem ao reiniciar)"
    )
    return secrets.token_urlsafe(32)


def _whatsapp_sender(client: WhatsAppClient) -> Sender:
    def send(contact: str, text: str) -> None:
        try:
            client.send_text(contact, text)
        except WhatsAppError as exc:
            raise DeliveryError(str(exc)) from exc

    return send


def _transcriber(settings: Settings) -> Transcriber | None:
    if not settings.transcription_enabled:
        return None
    return Transcriber(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),  # type: ignore[union-attr]
        model=settings.transcription_model,
        timeout_seconds=max(settings.llm_timeout_seconds, 30.0),
    )
