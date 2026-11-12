"""Converte um `Reply` em mensagem interativa do WhatsApp (botões ou lista).

Limites da Cloud API: até 3 botões com título de até 20 caracteres; lista com até 10 linhas,
título da linha de até 24 e descrição de até 72; corpo de até 1024 caracteres (botões) ou
4096 (lista). Quando não cabe, o bot manda texto com a lista numerada, que sempre funciona.

O id de cada opção carrega o que o cliente "digitaria": o número (opções numeradas) ou o próprio
rótulo. Assim a resposta passa pelo mesmo motor de qualquer mensagem de texto.
"""

from typing import Any

from app.conversation.reply import Reply

OPTION_ID_PREFIX = "opt:"
MAX_BUTTONS = 3
MAX_BUTTON_TITLE = 20
MAX_BUTTON_BODY = 1024
MAX_ROWS = 10
MAX_ROW_TITLE = 24
MAX_ROW_DESCRIPTION = 72
MAX_LIST_BODY = 4096
LIST_BUTTON_LABEL = "Ver opções"


def _values(reply: Reply) -> list[str]:
    if reply.numbered:
        return [str(i) for i in range(1, len(reply.options) + 1)]
    return list(reply.options)


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_interactive(reply: Reply) -> dict[str, Any] | None:
    """Corpo `interactive` da Cloud API, ou None se a resposta deve ir como texto."""
    options, values = list(reply.options), _values(reply)
    if not options:
        return None

    fits_buttons = (
        len(options) <= MAX_BUTTONS
        and all(len(o) <= MAX_BUTTON_TITLE for o in options)
        and len(reply.text) <= MAX_BUTTON_BODY
    )
    if fits_buttons:
        return {
            "type": "button",
            "body": {"text": reply.text},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": OPTION_ID_PREFIX + value, "title": title}}
                    for title, value in zip(options, values, strict=True)
                ]
            },
        }

    # Lista só para opções numeradas: o texto das outras (ex.: horários) já descreve as opções.
    if reply.numbered and len(options) <= MAX_ROWS and len(reply.text) <= MAX_LIST_BODY:
        rows = []
        for title, value in zip(options, values, strict=True):
            row: dict[str, str] = {
                "id": OPTION_ID_PREFIX + value,
                "title": _shorten(title, MAX_ROW_TITLE),
            }
            if len(title) > MAX_ROW_TITLE:
                row["description"] = _shorten(title, MAX_ROW_DESCRIPTION)
            rows.append(row)
        return {
            "type": "list",
            "body": {"text": reply.text},
            "action": {
                "button": LIST_BUTTON_LABEL,
                "sections": [{"title": "Opções", "rows": rows}],
            },
        }
    return None


def option_value(reply_id: str | None) -> str | None:
    """Valor enviado por um botão/linha criado por `build_interactive`."""
    if reply_id and reply_id.startswith(OPTION_ID_PREFIX):
        return reply_id.removeprefix(OPTION_ID_PREFIX) or None
    return None
