"""Ponto de entrada da aplicação web."""

from fastapi import FastAPI

from app import __version__
from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Assistente virtual de clínica",
        version=__version__,
        description="Bot de atendimento no WhatsApp para uma clínica fictícia.",
    )
    app.state.settings = settings

    @app.get("/health", tags=["status"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
