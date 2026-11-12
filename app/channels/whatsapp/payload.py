"""Leitura do payload do webhook da WhatsApp Cloud API.

Só interessam `messages` (com o nome do perfil em `contacts`). `statuses` (entregue, lido...)
são ignorados. O formato é tratado de forma defensiva: entrada malformada é descartada.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from app.channels.whatsapp.interactive import option_value

SUPPORTED_KINDS = frozenset({"text", "interactive", "button"})


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    wa_id: str
    kind: str
    text: str | None  # None = tipo não suportado (áudio, imagem...)
    profile_name: str | None = None
    media_id: str | None = None  # áudio: para baixar e transcrever
    mime_type: str | None = None

    @property
    def supported(self) -> bool:
        return self.text is not None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text_of(message: dict[str, Any]) -> str | None:
    kind = message.get("type")
    if kind == "text":
        body = _dict(message.get("text")).get("body")
    elif kind == "interactive":
        interactive = _dict(message.get("interactive"))
        choice = _dict(interactive.get("button_reply")) or _dict(interactive.get("list_reply"))
        # Botões/listas do próprio bot trazem no id o valor a "digitar" (ex.: o número).
        body = option_value(choice.get("id")) or choice.get("title")
    elif kind == "button":
        body = _dict(message.get("button")).get("text")
    else:
        return None
    return body if isinstance(body, str) and body.strip() else None


def parse_webhook(payload: Any) -> list[IncomingMessage]:
    return list(_iter_messages(_dict(payload)))


def _iter_messages(payload: dict[str, Any]) -> Iterator[IncomingMessage]:
    if payload.get("object") != "whatsapp_business_account":
        return
    for entry in _list(payload.get("entry")):
        for change in _list(_dict(entry).get("changes")):
            change = _dict(change)
            if change.get("field") != "messages":
                continue
            value = _dict(change.get("value"))
            names = {
                _dict(c).get("wa_id"): _dict(_dict(c).get("profile")).get("name")
                for c in _list(value.get("contacts"))
            }
            for raw in _list(value.get("messages")):
                message = _dict(raw)
                message_id, wa_id = message.get("id"), message.get("from")
                if not isinstance(message_id, str) or not isinstance(wa_id, str):
                    continue
                kind = str(message.get("type", "unknown"))
                audio = _dict(message.get("audio"))
                yield IncomingMessage(
                    message_id=message_id,
                    wa_id=wa_id,
                    kind=kind,
                    text=_text_of(message) if kind in SUPPORTED_KINDS else None,
                    profile_name=names.get(wa_id),
                    media_id=audio.get("id") if kind == "audio" else None,
                    mime_type=audio.get("mime_type") if kind == "audio" else None,
                )
