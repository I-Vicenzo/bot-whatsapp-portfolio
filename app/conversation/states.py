"""Estados do fluxo de conversa (o status bot/humano fica em `ConversationStatus`)."""

from enum import StrEnum


class State(StrEnum):
    MENU = "MENU"
    BOOK_SPECIALTY = "BOOK_SPECIALTY"
    BOOK_DAY = "BOOK_DAY"
    BOOK_TIME = "BOOK_TIME"
    BOOK_NAME = "BOOK_NAME"
    BOOK_CONFIRM = "BOOK_CONFIRM"
    CANCEL_PICK = "CANCEL_PICK"
    CANCEL_CONFIRM = "CANCEL_CONFIRM"


BOOKING_STATES = frozenset(
    {
        State.BOOK_SPECIALTY,
        State.BOOK_DAY,
        State.BOOK_TIME,
        State.BOOK_NAME,
        State.BOOK_CONFIRM,
    }
)
CANCEL_STATES = frozenset({State.CANCEL_PICK, State.CANCEL_CONFIRM})
