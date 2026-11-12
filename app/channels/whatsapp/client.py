"""Envio de mensagens pela WhatsApp Cloud API (Graph API)."""

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.channels.whatsapp.interactive import build_interactive
from app.conversation.reply import Reply
from app.logging_utils import mask_phone

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com"
MAX_TEXT_LENGTH = 4096
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class WhatsAppError(Exception):
    """Falha definitiva no envio (erro do cliente ou retries esgotados)."""


class WhatsAppClient:
    def __init__(
        self,
        *,
        token: str,
        phone_number_id: str,
        api_version: str,
        http: httpx.Client | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._graph = f"{GRAPH_URL}/{api_version}"
        self._url = f"{self._graph}/{phone_number_id}/messages"
        self._http = http or httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0))
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def send_text(self, to: str, text: str) -> str | None:
        """Envia texto e devolve o id da mensagem na Meta."""
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": text[:MAX_TEXT_LENGTH]},
        }
        data = self._post(body, to)
        messages = data.get("messages") or [{}]
        return messages[0].get("id")

    def send_interactive(self, to: str, interactive: dict[str, Any]) -> str | None:
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }
        data = self._post(body, to)
        messages = data.get("messages") or [{}]
        return messages[0].get("id")

    def send_template(self, to: str, name: str, language: str, parameters: list[str]) -> str | None:
        """Mensagem de template aprovado (única permitida fora da janela de 24 h)."""
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {
                "name": name,
                "language": {"code": language},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": p} for p in parameters],
                    }
                ],
            },
        }
        data = self._post(body, to)
        messages = data.get("messages") or [{}]
        return messages[0].get("id")

    def send_reply(self, to: str, reply: Reply, *, interactive: bool = True) -> str | None:
        """Botões ou lista quando cabem; senão, texto com a lista numerada."""
        payload = build_interactive(reply) if interactive else None
        if payload is not None:
            return self.send_interactive(to, payload)
        return self.send_text(to, reply.render())

    def download_media(self, media_id: str, max_bytes: int) -> tuple[bytes, str]:
        """Baixa uma mídia recebida (dois passos: metadados com a URL, depois o arquivo)."""
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            meta = self._http.get(f"{self._graph}/{media_id}", headers=headers)
            meta.raise_for_status()
            info = meta.json()
            if int(info.get("file_size") or 0) > max_bytes:
                raise WhatsAppError("mídia grande demais")
            media = self._http.get(info["url"], headers=headers)
            media.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise WhatsAppError(f"HTTP {exc.response.status_code} ao baixar mídia") from exc
        except httpx.HTTPError as exc:
            raise WhatsAppError(f"erro de rede ao baixar mídia ({type(exc).__name__})") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise WhatsAppError("metadados de mídia inesperados") from exc
        if len(media.content) > max_bytes:
            raise WhatsAppError("mídia grande demais")
        return media.content, str(info.get("mime_type") or "audio/ogg")

    def mark_as_read(self, message_id: str) -> None:
        self._post(
            {"messaging_product": "whatsapp", "status": "read", "message_id": message_id},
            to=None,
        )

    def _post(self, body: dict[str, Any], to: str | None) -> dict[str, Any]:
        target = mask_phone(to) if to else "-"
        headers = {"Authorization": f"Bearer {self._token}"}
        for attempt in range(self._max_retries + 1):
            last_attempt = attempt == self._max_retries
            try:
                response = self._http.post(self._url, json=body, headers=headers)
            except httpx.TransportError as exc:
                logger.warning("WhatsApp: erro de rede (%s) para %s", type(exc).__name__, target)
                if last_attempt:
                    raise WhatsAppError("erro de rede") from exc
                self._sleep(self._backoff * 2**attempt)
                continue

            if response.status_code in RETRY_STATUSES and not last_attempt:
                wait = _retry_after(response) or self._backoff * 2**attempt
                logger.warning(
                    "WhatsApp: HTTP %s para %s; nova tentativa em %.1fs",
                    response.status_code,
                    target,
                    wait,
                )
                self._sleep(wait)
                continue
            if response.is_error:
                logger.error(
                    "WhatsApp: envio para %s falhou com HTTP %s: %s",
                    target,
                    response.status_code,
                    _error_summary(response),
                )
                raise WhatsAppError(f"HTTP {response.status_code}")
            return response.json()
        raise WhatsAppError("tentativas esgotadas")  # pragma: no cover - laço sempre retorna


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After", "")
    return float(value) if value.isdigit() else None


def _error_summary(response: httpx.Response) -> str:
    """Código e mensagem do erro da Graph API (sem ecoar a requisição nem o token)."""
    try:
        error = response.json().get("error", {})
    except ValueError:
        return "resposta não-JSON"
    return f"code={error.get('code')} {error.get('message', '')}".strip()
