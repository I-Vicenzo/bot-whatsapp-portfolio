"""Configuração da aplicação via variáveis de ambiente (e `.env`, se existir)."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    DEV = "dev"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: AppEnv = AppEnv.DEV
    database_url: str = "sqlite:///./var/app.db"
    clinic_data_path: Path = Path("data/clinica.yaml")
    demo_seed: bool = True
    simulator_rate_limit_per_minute: int = 30

    session_secret: SecretStr | None = None
    admin_username: str = "admin"
    admin_password: SecretStr | None = None

    whatsapp_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_api_version: str = "v26.0"

    llm_enabled: bool = False
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: SecretStr | None = None
    llm_model: str = "openai/gpt-oss-20b"
    llm_timeout_seconds: float = 15.0
    llm_max_question_chars: int = 300
    llm_calls_per_contact_per_hour: int = 10
    llm_daily_cap: int = 200

    @property
    def whatsapp_enabled(self) -> bool:
        """WhatsApp só liga com todas as credenciais preenchidas."""
        return all(
            (
                self.whatsapp_token,
                self.whatsapp_phone_number_id,
                self.whatsapp_app_secret,
                self.whatsapp_verify_token,
            )
        )

    @property
    def ai_enabled(self) -> bool:
        return self.llm_enabled and self.llm_api_key is not None

    @property
    def demo_mode(self) -> bool:
        """Modo demo: nenhuma integração externa configurada."""
        return not self.whatsapp_enabled and not self.ai_enabled


@lru_cache
def get_settings() -> Settings:
    return Settings()
