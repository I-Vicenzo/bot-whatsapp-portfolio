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
    # Demo pública: a tela de login do painel mostra ADMIN_USERNAME/ADMIN_PASSWORD para
    # qualquer visitante. Por isso não pode rodar junto com o WhatsApp real.
    public_demo: bool = False
    simulator_rate_limit_per_minute: int = 30

    session_secret: SecretStr | None = None
    admin_username: str = "admin"
    admin_password: SecretStr | None = None

    whatsapp_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_api_version: str = "v26.0"
    whatsapp_interactive: bool = True  # botões e listas; false = só texto numerado
    reminders_enabled: bool = False  # exige template aprovado na Meta
    whatsapp_reminder_template: str = "lembrete_consulta"
    whatsapp_reminder_language: str = "pt_BR"
    reminder_hours_before: int = 24

    llm_enabled: bool = False
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: SecretStr | None = None
    llm_model: str = "openai/gpt-oss-20b"
    llm_timeout_seconds: float = 15.0
    llm_max_question_chars: int = 300
    audio_transcription_enabled: bool = False
    transcription_model: str = "whisper-large-v3"
    llm_calls_per_contact_per_hour: int = 10
    llm_daily_cap: int = 200

    def production_problems(self) -> list[str]:
        """Configurações inseguras que impedem iniciar (em produção, ou em qualquer ambiente
        no caso da demo pública com WhatsApp)."""
        problems = []
        if self.public_demo and self.whatsapp_enabled:
            problems.append(
                "PUBLIC_DEMO mostra o login do painel a qualquer visitante: não ligue o "
                "WhatsApp real na mesma instância"
            )
        if self.app_env is not AppEnv.PRODUCTION:
            return problems
        password = self.admin_password.get_secret_value() if self.admin_password else ""
        if len(password) < 12 or password == "admin":  # noqa: S105 - recusa a senha padrão
            problems.append("ADMIN_PASSWORD deve ter ao menos 12 caracteres e não ser a padrão")
        secret = self.session_secret.get_secret_value() if self.session_secret else ""
        if len(secret) < 32:
            problems.append("SESSION_SECRET deve ter ao menos 32 caracteres")
        return problems

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
    def transcription_enabled(self) -> bool:
        """Usa o mesmo provedor e chave da IA (Groq por padrão)."""
        return self.audio_transcription_enabled and self.llm_api_key is not None

    @property
    def demo_mode(self) -> bool:
        """Modo demo: nenhuma integração externa configurada."""
        return not self.whatsapp_enabled and not self.ai_enabled


@lru_cache
def get_settings() -> Settings:
    return Settings()
