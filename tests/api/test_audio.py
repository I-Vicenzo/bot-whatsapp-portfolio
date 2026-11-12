"""Áudios do WhatsApp: baixar pela Graph API, transcrever (Whisper) e tratar como texto."""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.client import LLMError
from app.ai.transcription import MAX_AUDIO_BYTES, Transcriber
from app.channels.whatsapp.client import WhatsAppClient, WhatsAppError
from app.channels.whatsapp.signature import sign
from app.clock import FixedClock
from app.config import Settings
from app.db.models import Message
from app.main import create_app

SECRET = "app-secret"
GRAPH = "https://graph.facebook.com/v26.0"
SEND_URL = f"{GRAPH}/123456/messages"
MEDIA_META_URL = f"{GRAPH}/media-1"
MEDIA_FILE_URL = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1"
WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
WA_ID = "5547999990001"


def audio_settings(settings: Settings, **extra: object) -> Settings:
    return Settings(
        _env_file=None,
        **{
            **settings.model_dump(),
            "whatsapp_token": "token",
            "whatsapp_phone_number_id": "123456",
            "whatsapp_app_secret": SECRET,
            "whatsapp_verify_token": "verify",
            "llm_api_key": "groq-key",
            "audio_transcription_enabled": True,
            **extra,
        },
    )


@pytest.fixture
def meta() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.post(SEND_URL).mock(
            return_value=httpx.Response(200, json={"messages": [{"id": "out"}]})
        )
        router.get(MEDIA_META_URL).mock(
            return_value=httpx.Response(
                200,
                json={"url": MEDIA_FILE_URL, "mime_type": "audio/ogg", "file_size": 1200},
            )
        )
        router.get(MEDIA_FILE_URL).mock(return_value=httpx.Response(200, content=b"OggS..."))
        router.post(WHISPER_URL).mock(
            return_value=httpx.Response(200, json={"text": " Quanto custa a consulta?  "})
        )
        yield router


def audio_webhook(message_id: str = "wamid.audio") -> dict[str, Any]:
    message = {
        "from": WA_ID,
        "id": message_id,
        "type": "audio",
        "audio": {"id": "media-1", "mime_type": "audio/ogg; codecs=opus", "voice": True},
    }
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": {"messages": [message]}}]}],
    }


def post(client: TestClient, payload: dict) -> None:
    body = json.dumps(payload).encode()
    response = client.post(
        "/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body, SECRET)}
    )
    assert response.status_code == 200


def sent(meta: respx.MockRouter) -> list[dict]:
    return [json.loads(c.request.content) for c in meta.calls if str(c.request.url) == SEND_URL]


def visible_text(body: dict) -> str:
    if body["type"] == "text":
        return body["text"]["body"]
    return body["interactive"]["body"]["text"]


def test_audio_is_transcribed_and_answered(
    settings: Settings, clock: FixedClock, meta: respx.MockRouter
) -> None:
    with TestClient(create_app(audio_settings(settings), clock=clock)) as client:
        post(client, audio_webhook())
        with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
            inbound = session.scalars(select(Message).where(Message.direction == "in")).one()

    [reply] = sent(meta)
    assert "R$ 280,50" in visible_text(reply)  # pergunta de preço respondida
    assert inbound.text == "🎤 Quanto custa a consulta?"

    whisper = next(c.request for c in meta.calls if str(c.request.url) == WHISPER_URL)
    assert whisper.headers["Authorization"] == "Bearer groq-key"
    assert b'name="model"' in whisper.content
    assert b"whisper-large-v3" in whisper.content
    assert b'name="language"' in whisper.content
    media = next(c.request for c in meta.calls if str(c.request.url) == MEDIA_FILE_URL)
    assert media.headers["Authorization"] == "Bearer token"


def test_duplicate_audio_is_not_transcribed_twice(
    settings: Settings, clock: FixedClock, meta: respx.MockRouter
) -> None:
    with TestClient(create_app(audio_settings(settings), clock=clock)) as client:
        post(client, audio_webhook())
        post(client, audio_webhook())
    assert meta.routes[3].call_count == 1  # Whisper
    assert len(sent(meta)) == 1


@pytest.mark.parametrize(
    ("route_index", "response"),
    [
        (1, httpx.Response(404)),  # metadados da mídia
        (2, httpx.Response(500)),  # arquivo
        (3, httpx.Response(429)),  # Whisper
        (3, httpx.Response(200, json={"text": "   "})),
    ],
)
def test_failures_fall_back_to_standard_reply(
    settings: Settings,
    clock: FixedClock,
    meta: respx.MockRouter,
    route_index: int,
    response: httpx.Response,
) -> None:
    meta.routes[route_index].mock(return_value=response)
    with TestClient(create_app(audio_settings(settings), clock=clock)) as client:
        post(client, audio_webhook())
    [reply] = sent(meta)
    assert "só consigo ler mensagens de texto" in reply["text"]["body"]


def test_media_too_large_is_not_downloaded(
    settings: Settings, clock: FixedClock, meta: respx.MockRouter
) -> None:
    meta.routes[1].mock(
        return_value=httpx.Response(
            200, json={"url": MEDIA_FILE_URL, "file_size": MAX_AUDIO_BYTES + 1}
        )
    )
    with TestClient(create_app(audio_settings(settings), clock=clock)) as client:
        post(client, audio_webhook())
    assert meta.routes[2].call_count == 0
    assert "só consigo ler" in sent(meta)[0]["text"]["body"]


def test_transcription_disabled_keeps_standard_reply(
    settings: Settings, clock: FixedClock, meta: respx.MockRouter
) -> None:
    disabled = audio_settings(settings, audio_transcription_enabled=False)
    with TestClient(create_app(disabled, clock=clock)) as client:
        post(client, audio_webhook())
    assert meta.routes[1].call_count == 0
    assert "só consigo ler" in sent(meta)[0]["text"]["body"]


@respx.mock
def test_transcriber_errors() -> None:
    transcriber = Transcriber(
        base_url="https://api.groq.com/openai/v1", api_key="k", model="m", timeout_seconds=1
    )
    with pytest.raises(LLMError, match="vazio"):
        transcriber.transcribe(b"", "audio/ogg")
    respx.post(WHISPER_URL).mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(LLMError, match="ConnectError"):
        transcriber.transcribe(b"a", "audio/ogg")
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(LLMError, match="inesperado"):
        transcriber.transcribe(b"a", "audio/ogg")
    transcriber.close()


@respx.mock
def test_media_download_network_error() -> None:
    respx.get(MEDIA_META_URL).mock(side_effect=httpx.ConnectError("x"))
    client = WhatsAppClient(token="t", phone_number_id="123456", api_version="v26.0")
    with pytest.raises(WhatsAppError, match="rede"):
        client.download_media("media-1", 100)
    respx.get(MEDIA_META_URL).mock(return_value=httpx.Response(200, json={"no_url": True}))
    with pytest.raises(WhatsAppError, match="inesperados"):
        client.download_media("media-1", 100)
    respx.get(MEDIA_META_URL).mock(return_value=httpx.Response(200, json={"url": MEDIA_FILE_URL}))
    respx.get(MEDIA_FILE_URL).mock(return_value=httpx.Response(200, content=b"x" * 101))
    with pytest.raises(WhatsAppError, match="grande demais"):
        client.download_media("media-1", 100)
