"""Bot explica por que um dia bloqueado não tem horários (ver tests/fixtures/clinic.yaml)."""

from collections.abc import Callable

import pytest

from tests.conversation.conftest import Chat


@pytest.mark.parametrize(
    ("specialty", "day", "reason"),
    [
        ("cardio", "19/11", "feriado municipal"),
        ("cardio", "17/11", "congresso do Dr. Bruno"),
        ("clinico", "19/11", "feriado municipal"),
    ],
)
def test_bot_explains_why_the_day_is_blocked(
    new_chat: Callable[[str], Chat], specialty: str, day: str, reason: str
) -> None:
    chat = new_chat("5547999990001")
    chat.send(f"agendar {specialty}")
    chat.send(day)
    assert f"não há atendimento nesse dia ({reason})" in chat.text
