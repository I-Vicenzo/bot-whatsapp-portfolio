"""Comando "apagar meus dados" (LGPD)."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.clock import FixedClock
from app.conversation import ConversationEngine, State
from app.conversation.service import ConversationService
from app.db.models import Appointment, AppointmentStatus, Channel, Conversation, Message
from app.nlu.intents import Intent, detect
from app.nlu.normalize import normalize
from tests.conversation.conftest import Chat

CONTACT = "5547999990001"


@pytest.mark.parametrize(
    "text",
    ["apagar meus dados", "Quero excluir todos os meus dados", "delete meus dados", "LGPD"],
)
def test_erase_intent(text: str) -> None:
    assert detect(normalize(text)) is Intent.ERASE_DATA


def book(chat: Chat, day: str, hour: str) -> None:
    for text in ["agendar cardio", day, hour, "Maria Silva", "sim"]:
        chat.send(text)


@pytest.fixture
def service(
    session_factory: sessionmaker[Session],
    conversation_engine: ConversationEngine,
    clock: FixedClock,
) -> ConversationService:
    return ConversationService(session_factory, conversation_engine, clock)


def test_erase_requires_confirmation(chat: Chat) -> None:
    chat.send("oi")
    chat.send("quero apagar meus dados")
    assert chat.state is State.ERASE_CONFIRM
    assert "Não dá para desfazer" in chat.text
    assert chat.options == ("Sim", "Não")
    chat.send("talvez")
    assert chat.state is State.ERASE_CONFIRM
    chat.send("não")
    assert "não apaguei nada" in chat.text
    assert chat.state is State.MENU


def test_erase_from_middle_of_booking(chat: Chat) -> None:
    chat.send("agendar cardio")
    chat.send("apagar meus dados")
    assert chat.state is State.ERASE_CONFIRM


def test_erase_cancels_future_anonymizes_and_deletes_conversation(
    service: ConversationService,
    session_factory: sessionmaker[Session],
    clock: FixedClock,
) -> None:
    for text in ["agendar cardio", "segunda", "10h", "Maria Silva", "sim"]:
        service.receive(Channel.SIMULATOR, CONTACT, text)
    # Uma consulta já passada: continua no histórico, mas anonimizada.
    for text in ["agendar clinico", "sexta", "10h", "Maria Silva", "sim"]:
        service.receive(Channel.SIMULATOR, CONTACT, text)
    for text in ["agendar cardio", "segunda", "11h", "Outro Paciente", "sim"]:
        service.receive(Channel.SIMULATOR, "5500000000002", text)
    clock.advance(timedelta(days=1, hours=2))  # sexta 11:00: a consulta de sexta já passou

    service.receive(Channel.SIMULATOR, CONTACT, "apagar meus dados")
    result = service.receive(Channel.SIMULATOR, CONTACT, "sim")

    assert "seus dados foram apagados" in result.replies[0].text
    assert result.message_ids == []
    with session_factory() as session:
        assert session.scalar(select(Conversation).where(Conversation.contact == CONTACT)) is None
        mine = session.scalars(
            select(Appointment).where(Appointment.patient_name == "Removido (LGPD)")
        ).all()
        assert len(mine) == 2
        assert all(a.patient_phone.startswith("removido-") for a in mine)
        future, past = sorted(mine, key=lambda a: a.starts_at, reverse=True)
        assert future.status == AppointmentStatus.CANCELLED
        assert past.status == AppointmentStatus.CONFIRMED  # histórico preservado, anônimo
        assert (
            session.scalars(select(Appointment).where(Appointment.patient_phone == CONTACT)).all()
            == []
        )
        others = session.scalars(select(Message)).all()
        assert others  # a conversa do outro cliente continua
        assert all(m.conversation_id != result.conversation_id for m in others)

    # O horário cancelado volta a ficar livre; e o contato recomeça do zero.
    again = service.receive(Channel.SIMULATOR, CONTACT, "xyz")
    assert "assistente virtual" in again.replies[0].text


def test_erase_from_simulator_keeps_whatsapp_patient_with_same_number(
    service: ConversationService, session_factory: sessionmaker[Session]
) -> None:
    """O simulador é público: "apagar meus dados" com o número de um paciente do WhatsApp não
    pode cancelar nem anonimizar as consultas dele."""
    for text in ["agendar cardio", "segunda", "10h", "Maria Silva", "sim"]:
        service.receive(Channel.WHATSAPP, CONTACT, text)

    service.receive(Channel.SIMULATOR, CONTACT, "minhas consultas")
    listed = service.receive(Channel.SIMULATOR, CONTACT, "minhas consultas")
    assert "Cardiologia" not in listed.replies[0].text
    service.receive(Channel.SIMULATOR, CONTACT, "apagar meus dados")
    service.receive(Channel.SIMULATOR, CONTACT, "sim")

    with session_factory() as session:
        appointment = session.scalars(select(Appointment)).one()
        assert appointment.patient_name == "Maria Silva"
        assert appointment.patient_phone == CONTACT
        assert appointment.status == AppointmentStatus.CONFIRMED
