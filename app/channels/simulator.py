"""API do simulador web: o mesmo motor de produção, sem WhatsApp."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.conversation.service import ConversationService
from app.db.models import Channel
from app.ratelimit import RateLimiter

CONTACT_PATTERN = r"^\d{8,15}$"
MAX_MESSAGE_LENGTH = 1000

Contact = Annotated[
    str, Field(pattern=CONTACT_PATTERN, description="Número fictício, só dígitos (8 a 15)")
]

router = APIRouter(prefix="/api/simulator", tags=["simulador"])


class SendIn(BaseModel):
    contact: Contact
    text: Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)]
    name: Annotated[str | None, Field(max_length=60)] = None


class ReplyOut(BaseModel):
    text: str
    options: list[str]
    numbered: bool


class DebugOut(BaseModel):
    state: str
    status: str
    intent: str | None
    source: str


class SendOut(BaseModel):
    replies: list[ReplyOut]
    debug: DebugOut | None
    last_message_id: int


class MessageOut(BaseModel):
    id: int
    author: str
    text: str
    created_at: datetime


class ResetIn(BaseModel):
    contact: Contact


def _service(request: Request) -> ConversationService:
    return request.app.state.conversation_service


def _limiter(request: Request) -> RateLimiter:
    return request.app.state.simulator_limiter


@router.post("/messages", summary="Envia uma mensagem como cliente")
def send_message(body: SendIn, request: Request) -> SendOut:
    client_ip = request.client.host if request.client else "unknown"
    if not _limiter(request).allow(client_ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Muitas mensagens. Aguarde um pouco."
        )
    if not body.text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Mensagem vazia.")

    result = _service(request).receive(
        Channel.SIMULATOR, body.contact, body.text, display_name=body.name
    )
    debug = result.debug
    return SendOut(
        replies=[
            ReplyOut(text=r.text, options=list(r.options), numbered=r.numbered)
            for r in result.replies
        ],
        debug=DebugOut(**debug.__dict__) if debug else None,
        last_message_id=max(result.message_ids, default=0),
    )


@router.get("/messages", summary="Histórico e novas mensagens (polling)")
def list_messages(
    request: Request,
    contact: Annotated[str, Query(pattern=CONTACT_PATTERN)],
    after: Annotated[int, Query(ge=0)] = 0,
) -> list[MessageOut]:
    messages = _service(request).messages_after(Channel.SIMULATOR, contact, after)
    return [
        MessageOut(id=m.id, author=m.author, text=m.text, created_at=m.created_at) for m in messages
    ]


@router.post(
    "/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Apaga a conversa (as consultas continuam)",
)
def reset(body: ResetIn, request: Request) -> Response:
    _service(request).reset(Channel.SIMULATOR, body.contact)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
