from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.clock import FixedClock
from app.conversation import ConversationEngine
from app.conversation.service import ConversationService
from app.db.models import Channel, Conversation, Message


def make_service(
    session_factory: sessionmaker[Session],
    conversation_engine: ConversationEngine,
    clock: FixedClock,
) -> ConversationService:
    return ConversationService(session_factory, conversation_engine, clock)


def test_duplicate_external_id_is_ignored(
    session_factory: sessionmaker[Session],
    conversation_engine: ConversationEngine,
    clock: FixedClock,
) -> None:
    service = make_service(session_factory, conversation_engine, clock)
    first = service.receive(Channel.WHATSAPP, "5547999990001", "oi", external_id="wamid.1")
    again = service.receive(Channel.WHATSAPP, "5547999990001", "oi", external_id="wamid.1")

    assert first.replies
    assert not first.duplicate
    assert again.duplicate
    assert again.replies == []
    with session_factory() as session:
        assert len(session.scalars(select(Message)).all()) == 2  # entrada + 1 resposta


def test_display_name_is_saved(
    session_factory: sessionmaker[Session],
    conversation_engine: ConversationEngine,
    clock: FixedClock,
) -> None:
    service = make_service(session_factory, conversation_engine, clock)
    service.receive(Channel.WHATSAPP, "5547999990001", "oi", display_name="Ana")
    with session_factory() as session:
        conversation = session.scalar(select(Conversation))
        assert conversation is not None
        assert conversation.display_name == "Ana"


def test_messages_record_authors_and_direction(
    session_factory: sessionmaker[Session],
    conversation_engine: ConversationEngine,
    clock: FixedClock,
) -> None:
    service = make_service(session_factory, conversation_engine, clock)
    service.receive(Channel.SIMULATOR, "5547999990001", "oi")
    messages = service.messages_after(Channel.SIMULATOR, "5547999990001")
    assert [(m.direction, m.author) for m in messages] == [("in", "customer"), ("out", "bot")]
    assert service.messages_after(Channel.WHATSAPP, "5547999990001") == []
