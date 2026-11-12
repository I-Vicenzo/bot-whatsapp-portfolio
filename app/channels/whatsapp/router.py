"""Webhook da WhatsApp Cloud API.

POST: valida a assinatura sobre o corpo bruto, responde 200 na hora e processa em background
(a Meta reenvia o webhook se não receber 200 rápido; a deduplicação cobre reenvios).
"""

import hmac
import json
import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.ai.client import LLMError
from app.ai.transcription import MAX_AUDIO_BYTES, Transcriber
from app.channels.whatsapp.client import WhatsAppClient, WhatsAppError
from app.channels.whatsapp.payload import IncomingMessage, parse_webhook
from app.channels.whatsapp.signature import verify_signature
from app.config import Settings
from app.conversation.service import ConversationService
from app.db.models import Channel
from app.logging_utils import mask_phone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook/whatsapp", tags=["whatsapp"])


@dataclass(frozen=True)
class WhatsAppOptions:
    interactive: bool = True
    transcriber: Transcriber | None = None


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    if not settings.whatsapp_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WhatsApp não configurado.")
    return settings


@router.get("", summary="Verificação do webhook pela Meta", response_class=PlainTextResponse)
def verify(
    request: Request,
    mode: Annotated[str, Query(alias="hub.mode")] = "",
    token: Annotated[str, Query(alias="hub.verify_token")] = "",
    challenge: Annotated[str, Query(alias="hub.challenge")] = "",
) -> str:
    settings = _settings(request)
    expected = settings.whatsapp_verify_token.get_secret_value()  # type: ignore[union-attr]
    if mode == "subscribe" and hmac.compare_digest(token.encode(), expected.encode()):
        return challenge
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Token de verificação inválido.")


@router.post("", summary="Recebe mensagens da Meta")
async def receive(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    settings = _settings(request)
    body = await request.body()
    secret = settings.whatsapp_app_secret.get_secret_value()  # type: ignore[union-attr]
    if not verify_signature(body, request.headers.get("X-Hub-Signature-256"), secret):
        logger.warning("WhatsApp: webhook com assinatura inválida ou ausente")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Assinatura inválida.")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "JSON inválido.") from None

    messages = parse_webhook(payload)
    if messages:
        background_tasks.add_task(
            process_messages,
            request.app.state.conversation_service,
            request.app.state.whatsapp_client,
            messages,
            request.app.state.whatsapp_options,
        )
    return {"status": "ok"}


def process_messages(
    service: ConversationService,
    client: WhatsAppClient,
    messages: list[IncomingMessage],
    options: WhatsAppOptions | None = None,
) -> None:
    options = options or WhatsAppOptions()
    for message in messages:
        try:
            _process(service, client, message, options)
        except Exception:
            # Uma mensagem com erro não derruba as outras do mesmo lote.
            logger.exception(
                "WhatsApp: erro ao processar mensagem de %s", mask_phone(message.wa_id)
            )


def _process(
    service: ConversationService,
    client: WhatsAppClient,
    message: IncomingMessage,
    options: WhatsAppOptions,
) -> None:
    text, stored, unsupported = message.text or "", None, None
    if not message.supported:
        unsupported = message.kind
        transcript = _transcribe(service, client, options.transcriber, message)
        if transcript:
            text, stored, unsupported = transcript, f"🎤 {transcript}", None

    result = service.receive(
        Channel.WHATSAPP,
        message.wa_id,
        text,
        display_name=message.profile_name,
        external_id=message.message_id,
        unsupported_kind=unsupported,
        stored_text=stored,
    )
    if result.duplicate:
        logger.info("WhatsApp: mensagem repetida ignorada (%s)", message.message_id)
        return
    for reply in result.replies:
        try:
            client.send_reply(message.wa_id, reply, interactive=options.interactive)
        except WhatsAppError:
            return  # já registrado no log pelo cliente; não insiste nas próximas


def _transcribe(
    service: ConversationService,
    client: WhatsAppClient,
    transcriber: Transcriber | None,
    message: IncomingMessage,
) -> str | None:
    """Texto do áudio, ou None (sem transcritor, reenvio, erro): o bot responde que só lê texto."""
    if transcriber is None or message.kind != "audio" or not message.media_id:
        return None
    if service.already_received(message.message_id):
        return None
    try:
        audio, mime_type = client.download_media(message.media_id, MAX_AUDIO_BYTES)
        return transcriber.transcribe(audio, message.mime_type or mime_type)
    except (WhatsAppError, LLMError) as exc:
        logger.warning("WhatsApp: áudio não transcrito (%s)", exc)
        return None
