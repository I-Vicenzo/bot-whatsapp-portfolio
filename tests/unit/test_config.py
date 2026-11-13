from app.config import Settings


def test_empty_values_in_env_file_mean_not_configured() -> None:
    """`.env` com `LLM_API_KEY=` ou `WHATSAPP_TOKEN=` vazios (ainda não preenchidos) não liga
    a integração com credencial vazia."""
    settings = Settings(
        _env_file=None,
        llm_enabled=True,
        llm_api_key="",
        audio_transcription_enabled=True,
        whatsapp_token="",
        whatsapp_phone_number_id="",
        whatsapp_app_secret="",
        whatsapp_verify_token="algum-token",
    )
    assert not settings.ai_enabled
    assert not settings.transcription_enabled
    assert not settings.whatsapp_enabled
    assert settings.demo_mode


def test_filled_key_enables_ai() -> None:
    settings = Settings(_env_file=None, llm_enabled=True, llm_api_key="gsk_x")
    assert settings.ai_enabled
