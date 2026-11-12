"""Ponto único de entrada das mensagens de todos os canais.

WhatsApp, simulador e painel chamam este serviço; ele grava a mensagem recebida (deduplicando
pelo id externo), roda o motor e grava as respostas. O simulador, portanto, exercita exatamente
a mesma lógica de produção.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock
from app.conversation import texts
from app.conversation.engine import ConversationEngine, EngineResult, Source
from app.conversation.reply import Reply
from app.conversation.states import State
from app.db.models import Author, Channel, Conversation, ConversationStatus, Message, Priority

logger = logging.getLogger(__name__)

# Fora desta janela desde a última mensagem do cliente, a Meta só aceita templates aprovados.
WHATSAPP_WINDOW = timedelta(hours=24)

# Envia texto para um contato no canal. Canais sem envio ativo (simulador) entregam por polling.
Sender = Callable[[str, str], None]


class ConversationNotFound(Exception):
    pass


class WindowClosed(Exception):
    """Janela de 24 h do WhatsApp fechada: só templates aprovados podem ser enviados."""


class DeliveryError(Exception):
    """O canal não aceitou a mensagem."""


@dataclass
class Debug:
    """O que o painel "por dentro do bot" mostra."""

    state: str
    status: str
    intent: str | None
    source: str


@dataclass
class InboundResult:
    conversation_id: int
    replies: list[Reply] = field(default_factory=list)
    message_ids: list[int] = field(default_factory=list)
    duplicate: bool = False
    debug: Debug | None = None


class ConversationService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        engine: ConversationEngine,
        clock: Clock,
        senders: dict[Channel, Sender] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.engine = engine
        self.clock = clock
        self.senders = senders or {}

    def receive(
        self,
        channel: Channel,
        contact: str,
        text: str,
        *,
        display_name: str | None = None,
        external_id: str | None = None,
        unsupported_kind: str | None = None,
        stored_text: str | None = None,
    ) -> InboundResult:
        """Processa uma mensagem do cliente. `unsupported_kind` (ex.: "audio") registra a
        mensagem sem passar pelo motor e responde que só entendemos texto."""
        with self.session_factory.begin() as session:
            conversation = self.get_or_create(session, channel, contact)
            if display_name:
                conversation.display_name = display_name

            if unsupported_kind:
                stored = f"[mensagem do tipo {unsupported_kind}]"
            else:
                stored = stored_text or text
            inbound = self._add_message(session, conversation, Author.CUSTOMER, stored, external_id)
            if inbound is None:
                return InboundResult(conversation.id, duplicate=True)

            if unsupported_kind:
                result = self._unsupported(conversation)
            else:
                result = self.engine.handle(session, conversation, text)
            ids = [inbound.id]
            author = Author.AI if result.source is Source.AI else Author.BOT
            for reply in result.replies:
                message = self._add_message(session, conversation, author, reply.render())
                if message is not None:  # sem external_id nunca é duplicata
                    ids.append(message.id)

            return InboundResult(
                conversation_id=conversation.id,
                replies=result.replies,
                message_ids=ids,
                debug=Debug(
                    state=conversation.state,
                    status=conversation.status,
                    intent=result.intent,
                    source=str(result.source if result.replies else Source.SILENT),
                ),
            )

    def _unsupported(self, conversation: Conversation) -> EngineResult:
        conversation.last_inbound_at = self.clock.local_now()  # abre a janela de 24 h
        if conversation.status != ConversationStatus.BOT:
            return EngineResult([], source=Source.SILENT)  # o atendente vê o registro
        return EngineResult([Reply(texts.UNSUPPORTED_MESSAGE)], "unsupported")

    def already_received(self, external_id: str) -> bool:
        """Atalho para evitar trabalho caro (ex.: transcrição) em reenvios da Meta. A garantia
        continua sendo o índice único em `receive`."""
        with self.session_factory() as session:
            found = session.scalar(select(Message.id).where(Message.external_id == external_id))
            return found is not None

    def get_or_create(self, session: Session, channel: Channel, contact: str) -> Conversation:
        conversation = session.scalar(
            select(Conversation).where(
                Conversation.channel == channel, Conversation.contact == contact
            )
        )
        if conversation is None:
            conversation = Conversation(
                channel=channel, contact=contact, created_at=self.clock.local_now()
            )
            session.add(conversation)
            session.flush()
        return conversation

    def _add_message(
        self,
        session: Session,
        conversation: Conversation,
        author: Author,
        text: str,
        external_id: str | None = None,
    ) -> Message | None:
        """Grava a mensagem. Com `external_id` repetido (reenvio da Meta), devolve None."""
        message = Message(
            conversation_id=conversation.id,
            direction="in" if author is Author.CUSTOMER else "out",
            author=author,
            text=text,
            external_id=external_id,
            created_at=self.clock.local_now(),
        )
        try:
            with session.begin_nested():
                session.add(message)
        except IntegrityError as exc:
            if external_id is not None and "messages.external_id" in str(exc.orig):
                return None
            raise
        return message

    def messages_after(
        self, channel: Channel, contact: str, after_id: int = 0, limit: int = 200
    ) -> list[Message]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(Message)
                    .join(Conversation)
                    .where(
                        Conversation.channel == channel,
                        Conversation.contact == contact,
                        Message.id > after_id,
                    )
                    .order_by(Message.id)
                    .limit(limit)
                )
            )

    def reset(self, channel: Channel, contact: str) -> None:
        """Apaga a conversa e o histórico (consultas continuam)."""
        with self.session_factory.begin() as session:
            conversation = session.scalar(
                select(Conversation).where(
                    Conversation.channel == channel, Conversation.contact == contact
                )
            )
            if conversation is not None:
                session.delete(conversation)

    # --- atendimento humano (painel) -------------------------------------------------------

    def window_open(self, conversation: Conversation) -> bool:
        if conversation.channel != Channel.WHATSAPP:
            return True
        last = conversation.last_inbound_at
        return last is not None and self.clock.local_now() - last < WHATSAPP_WINDOW

    def _get(self, session: Session, conversation_id: int) -> Conversation:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise ConversationNotFound(str(conversation_id))
        return conversation

    def _deliver(self, conversation: Conversation, text: str) -> None:
        sender = self.senders.get(Channel(conversation.channel))
        if sender is None:
            return  # simulador: o cliente busca por polling
        sender(conversation.contact, text)

    def assign(self, conversation_id: int, user_id: int) -> None:
        with self.session_factory.begin() as session:
            conversation = self._get(session, conversation_id)
            conversation.status = ConversationStatus.HUMAN
            conversation.assigned_to = user_id

    def agent_reply(self, conversation_id: int, user_id: int, text: str) -> Message:
        """Entrega a resposta pelo canal da conversa e grava com autor `agent`. Responder
        também assume a conversa (o bot para de responder)."""
        with self.session_factory.begin() as session:
            conversation = self._get(session, conversation_id)
            if not self.window_open(conversation):
                raise WindowClosed(str(conversation_id))
            self._deliver(conversation, text)  # falha aqui: nada é gravado
            conversation.status = ConversationStatus.HUMAN
            if conversation.assigned_to is None:
                conversation.assigned_to = user_id
            message = self._add_message(session, conversation, Author.AGENT, text)
            if message is None:  # pragma: no cover - sem external_id não há duplicata
                raise RuntimeError("mensagem do atendente não gravada")
            return message

    def release(self, conversation_id: int) -> None:
        """Devolve a conversa ao bot e avisa o cliente (se o canal permitir)."""
        with self.session_factory.begin() as session:
            conversation = self._get(session, conversation_id)
            conversation.status = ConversationStatus.BOT
            conversation.priority = Priority.NORMAL
            conversation.assigned_to = None
            conversation.handoff_reason = None
            conversation.handoff_at = None
            conversation.state = State.MENU
            conversation.context = {}
            conversation.unknown_count = 0
            if self.window_open(conversation):
                try:
                    self._deliver(conversation, texts.RELEASED)
                except DeliveryError:
                    logger.warning("Aviso de fim de atendimento não entregue")
                    return
                self._add_message(session, conversation, Author.BOT, texts.RELEASED)
