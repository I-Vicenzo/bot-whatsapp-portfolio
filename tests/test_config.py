import pytest

from app.config import AppEnv, Settings

WHATSAPP_VARS = {
    "WHATSAPP_TOKEN": "token",
    "WHATSAPP_PHONE_NUMBER_ID": "123",
    "WHATSAPP_APP_SECRET": "secret",
    "WHATSAPP_VERIFY_TOKEN": "verify",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [*WHATSAPP_VARS, "LLM_ENABLED", "LLM_API_KEY", "APP_ENV"]:
        monkeypatch.delenv(name, raising=False)


def test_defaults_without_env_are_demo_mode() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env is AppEnv.DEV
    assert settings.demo_mode
    assert not settings.whatsapp_enabled
    assert not settings.ai_enabled


def test_whatsapp_requires_all_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in WHATSAPP_VARS.items():
        monkeypatch.setenv(name, value)
    assert Settings(_env_file=None).whatsapp_enabled

    monkeypatch.delenv("WHATSAPP_APP_SECRET")
    assert not Settings(_env_file=None).whatsapp_enabled


@pytest.mark.parametrize(
    ("enabled", "api_key", "expected"),
    [("true", "key", True), ("true", None, False), ("false", "key", False)],
)
def test_ai_requires_flag_and_key(
    monkeypatch: pytest.MonkeyPatch, enabled: str, api_key: str | None, expected: bool
) -> None:
    monkeypatch.setenv("LLM_ENABLED", enabled)
    if api_key:
        monkeypatch.setenv("LLM_API_KEY", api_key)

    settings = Settings(_env_file=None)

    assert settings.ai_enabled is expected
    assert settings.demo_mode is not expected


def test_secrets_are_not_exposed_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHATSAPP_TOKEN", "super-secret-token")

    assert "super-secret-token" not in repr(Settings(_env_file=None))
