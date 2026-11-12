from app.channels.whatsapp.interactive import build_interactive, option_value
from app.conversation.reply import Reply


def test_no_options_is_text() -> None:
    assert build_interactive(Reply("Olá")) is None


def test_up_to_three_short_options_are_buttons() -> None:
    payload = build_interactive(Reply("Escolha", ("Clínica geral", "Cardiologia")))
    assert payload is not None
    assert payload["type"] == "button"
    ids = [b["reply"]["id"] for b in payload["action"]["buttons"]]
    assert ids == ["opt:1", "opt:2"]


def test_long_button_title_falls_back_to_list() -> None:
    payload = build_interactive(Reply("Escolha", ("Um título bem comprido demais", "Outro")))
    assert payload is not None
    assert payload["type"] == "list"


def test_more_than_ten_numbered_options_is_text() -> None:
    assert build_interactive(Reply("Escolha", tuple(str(i) for i in range(11)))) is None


def test_long_unnumbered_options_are_text() -> None:
    reply = Reply("Horários", tuple(f"{h:02d}:00" for h in range(8, 18)), numbered=False)
    assert build_interactive(reply) is None


def test_long_body_is_text() -> None:
    assert build_interactive(Reply("x" * 1025, ("Sim", "Não"), numbered=False)) is None
    assert build_interactive(Reply("x" * 4097, tuple("abcd"))) is None


def test_long_row_title_is_shortened_with_description() -> None:
    title = "Cardiologia com Dr. Rafael Moreira, sexta-feira, 13/11 às 10:00 (paciente: Ana Lima)"
    payload = build_interactive(Reply("Qual?", (title, "Outra")))
    assert payload is not None
    row = payload["action"]["sections"][0]["rows"][0]
    assert len(row["title"]) == 24
    assert row["title"].endswith("…")
    assert len(row["description"]) == 72


def test_option_value() -> None:
    assert option_value("opt:3") == "3"
    assert option_value("opt:") is None
    assert option_value("outro-id") is None
    assert option_value(None) is None
