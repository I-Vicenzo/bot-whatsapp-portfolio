"""Ponto único de entrada das mensagens de todos os canais.

WhatsApp, simulador e painel chamam este serviço; ele grava a mensagem recebida (deduplicando
pelo id externo), roda o motor e grava as respostas. O simulador, portanto, exercita exatamente
a mesma lógica de produção.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock
from app.conversation import texts
from app.conversation.engine import ConversationEngine, EngineResult, Source
from app.conversation.reply import Reply
from app.db.models import Author, Channel, Conversation, ConversationStatus, Message


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
    ) -> None:
        self.session_factory = session_factory
        self.engine = engine
        self.clock = clock

    def receive(
        self,
        channel: Channel,
        contact: str,
        text: str,
        *,
        display_name: str | None = None,
        external_id: str | None = None,
        unsupported_kind: str | None = None,
    ) -> InboundResult:
        """Processa uma mensagem do cliente. `unsupported_kind` (ex.: "audio") registra a
        mensagem sem passar pelo motor e responde que só entendemos texto."""
        with self.session_factory.begin() as session:
            conversation = self.get_or_create(session, channel, contact)
            if display_name:
                conversation.display_name = display_name

            stored = f"[mensagem do tipo {unsupported_kind}]" if unsupported_kind else text
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
