from collections.abc import Callable
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.clinic import ClinicData
from app.clock import FixedClock
from app.conversation import ConversationEngine, EngineResult, Reply, State
from app.db.models import Appointment, Channel, Conversation
from app.scheduling import Scheduler


class Chat:
    """Cliente de teste: envia mensagens ao motor como um canal faria (uma transação por
    mensagem) e guarda o resultado para as verificações."""

    def __init__(
        self,
        engine: ConversationEngine,
        session_factory: sessionmaker[Session],
        clock: FixedClock,
        contact: str,
    ) -> None:
        self.engine = engine
        self.session_factory = session_factory
        self.clock = clock
        self.contact = contact
        self.result = EngineResult([])

    def send(self, text: str) -> list[Reply]:
        with self.session_factory.begin() as session:
            conversation = self._conversation(session)
            self.result = self.engine.handle(session, conversation, text)
        return self.result.replies

    def _conversation(self, session: Session) -> Conversation:
        conversation = session.scalar(
            select(Conversation).where(
                Conversation.channel == Channel.SIMULATOR, Conversation.contact == self.contact
            )
        )
        if conversation is None:
            conversation = Conversation(
                channel=Channel.SIMULATOR,
                contact=self.contact,
                created_at=self.clock.local_now(),
            )
            session.add(conversation)
            session.flush()
        return conversation

    @property
    def conversation(self) -> Conversation:
        with self.session_factory() as session:
            return self._conversation(session)

    @property
    def state(self) -> State:
        return State(self.conversation.state)

    @property
    def text(self) -> str:
        """Todas as respostas da última mensagem, como o WhatsApp mostraria."""
        return "\n\n".join(reply.render() for reply in self.result.replies)

    @property
    def options(self) -> tuple[str, ...]:
        return self.result.replies[-1].options if self.result.replies else ()

    def appointments(self) -> list[Appointment]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(Appointment)
                    .where(Appointment.patient_phone == self.contact)
                    .order_by(Appointment.id)
                )
            )


@pytest.fixture
def conversation_engine(
    clinic: ClinicData, scheduler: Scheduler, clock: FixedClock
) -> ConversationEngine:
    return ConversationEngine(clinic, scheduler, clock)


@pytest.fixture
def new_chat(
    conversation_engine: ConversationEngine,
    session_factory: sessionmaker[Session],
    clock: FixedClock,
) -> Callable[[str], Chat]:
    def make(contact: str = "5547999990001") -> Chat:
        return Chat(conversation_engine, session_factory, clock, contact)

    return make


@pytest.fixture
def chat(new_chat: Callable[[str], Chat]) -> Chat:
    return new_chat("5547999990001")


@pytest.fixture
def monday_10() -> datetime:
    return datetime(2026, 11, 16, 10, 0)
