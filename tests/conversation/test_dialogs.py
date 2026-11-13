"""Diálogos completos. Agora: quinta-feira, 12/11/2026, 09:00 (ver tests/conftest.py)."""

from collections.abc import Callable
from datetime import datetime, timedelta

import pytest

from app.clock import FixedClock
from app.conversation import Source, State
from app.db.models import AppointmentStatus, Channel, ConversationStatus, Priority
from app.scheduling import Scheduler
from tests.conversation.conftest import Chat


def book_monday(chat: Chat, hour: str = "10h", name: str = "Maria Silva") -> None:
    chat.send("quero agendar cardiologia")
    chat.send("segunda")
    chat.send(hour)
    chat.send(name)
    chat.send("sim")


# --- primeiro contato e menu -------------------------------------------------------------


def test_first_message_gets_greeting_and_menu(chat: Chat) -> None:
    chat.send("olá")
    assert "assistente virtual da *Clínica Teste*" in chat.text
    assert chat.options[0] == "Agendar consulta"
    assert "*1.* Agendar consulta" in chat.text
    assert chat.state is State.MENU


def test_unknown_first_message_still_greets(chat: Chat) -> None:
    chat.send("xyz")
    assert "Clínica Teste" in chat.text
    assert chat.result.source is not Source.NOT_UNDERSTOOD


def test_not_understood_three_times_offers_human(chat: Chat) -> None:
    chat.send("oi")
    chat.send("blablabla")
    assert "não entendi" in chat.text
    chat.send("hmmm")
    assert "atendente" not in chat.text.split("opções")[0]
    chat.send("??? nada")
    assert "digite *atendente*" in chat.text
    assert chat.conversation.unknown_count == 3
    chat.send("menu")
    assert chat.conversation.unknown_count == 0


def test_thanks(chat: Chat) -> None:
    chat.send("oi")
    chat.send("muito obrigada!")
    assert "Por nada" in chat.text


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("4", "R$ 280,50"),
        ("quanto custa?", "Dr. Bruno Teste"),
        ("5", "Plano Alfa, Plano Beta"),
        ("aceita convênio?", "Plano Alfa"),
        ("6", "Segunda a sexta: 08:00 às 12:00 e 13:30 às 18:00"),
        ("qual o endereço", "Rua Teste, 1"),
        ("que horas fecha?", "Domingo: fechado"),
        ("tem estacionamento?", "estacionamento gratuito"),
        ("aceita pix?", "Aceitamos Pix"),
    ],
)
def test_information(chat: Chat, message: str, expected: str) -> None:
    chat.send("oi")
    chat.send(message)
    assert expected in chat.text
    assert chat.state is State.MENU


def test_faq_wins_over_generic_intent(chat: Chat) -> None:
    chat.send("oi")
    chat.send("qual o valor do estacionamento?")
    assert chat.result.source is Source.FAQ


# --- agendamento -------------------------------------------------------------------------


def test_full_booking_by_menu_numbers(chat: Chat) -> None:
    chat.send("oi")
    chat.send("1")
    assert chat.state is State.BOOK_SPECIALTY
    assert chat.options == ("Clínica geral", "Cardiologia")

    chat.send("2")
    assert chat.state is State.BOOK_DAY
    assert chat.options[:3] == ("Qui 12/11", "Sex 13/11", "Sáb 14/11")
    assert "Dom 15/11" not in chat.options

    chat.send("4")  # Seg 16/11
    assert chat.state is State.BOOK_TIME
    assert "segunda-feira, 16/11" in chat.text
    assert "Manhã: 08:00, 08:30" in chat.text
    assert "Tarde: 13:30" in chat.text
    assert not chat.result.replies[-1].numbered

    chat.send("14:30")
    assert chat.state is State.BOOK_NAME

    chat.send("maria da silva")
    assert chat.state is State.BOOK_CONFIRM
    for expected in ("Cardiologia", "Dr. Bruno Teste", "16/11", "14:30", "Maria da Silva"):
        assert expected in chat.text
    assert "R$ 280,50" in chat.text

    chat.send("sim")
    assert "Consulta confirmada" in chat.text
    assert chat.state is State.MENU
    assert chat.conversation.context == {}
    [appointment] = chat.appointments()
    assert appointment.starts_at == datetime(2026, 11, 16, 14, 30)
    assert appointment.patient_name == "Maria da Silva"
    assert appointment.channel == "simulator"


def test_booking_with_free_text(chat: Chat, monday_10: datetime) -> None:
    chat.send("Bom dia, queria marcar com o cardiologista")
    assert chat.state is State.BOOK_DAY  # especialidade reconhecida na 1ª mensagem
    chat.send("segunda-feira")
    chat.send("10h")
    chat.send("João Pereira")
    chat.send("pode confirmar")
    assert chat.appointments()[0].starts_at == monday_10


def test_specialty_by_alias(chat: Chat) -> None:
    chat.send("agendar")
    chat.send("clínico")
    assert "*Clínica geral*" in chat.text


def test_day_typed_as_date_outside_offered_list(chat: Chat) -> None:
    chat.send("agendar cardio")
    chat.send("24/11")  # fora dos 7 primeiros oferecidos, mas dentro da janela
    assert chat.state is State.BOOK_TIME
    assert "terça-feira, 24/11" in chat.text


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("domingo", "a clínica não abre nesse dia"),
        ("10/11/2026", "essa data já passou"),
        ("20/12", "a agenda ainda não está aberta"),
    ],
)
def test_unavailable_day(chat: Chat, message: str, reason: str) -> None:
    chat.send("agendar cardio")
    chat.send(message)
    assert reason in chat.text
    assert chat.state is State.BOOK_DAY
    assert chat.options  # lista de dias repetida


def test_full_day(new_chat: Callable[[str], Chat], clock: FixedClock) -> None:
    clock.set(datetime(2026, 11, 13, 9, 0, tzinfo=clock.tz))  # sexta; sábado 08-12
    for i, hour in enumerate(["8h", "8:30", "9h", "9:30", "10h", "10:30", "11h", "11:30"]):
        other = new_chat(f"55000000{i:02d}")
        other.send("agendar cardio")
        other.send("sábado")
        other.send(hour)
        other.send("Fulano Tal")
        other.send("sim")
    chat = new_chat("5547999990001")
    chat.send("agendar cardio")
    assert "Sáb 14/11" not in chat.options
    chat.send("sábado")
    assert "todos os horários estão ocupados" in chat.text


@pytest.mark.parametrize(
    ("state_messages", "invalid", "expected_state", "hint"),
    [
        ([], "ortopedia", State.BOOK_SPECIALTY, "Não encontrei essa especialidade"),
        (["cardio"], "qualquer um", State.BOOK_DAY, "Não entendi a data"),
        (["cardio", "segunda"], "de manhã", State.BOOK_TIME, "Não entendi o horário"),
        (["cardio", "segunda", "10h"], "Maria", State.BOOK_NAME, "nome e sobrenome"),
        (["cardio", "segunda", "10h", "Ana Lima"], "talvez", State.BOOK_CONFIRM, "*sim*"),
    ],
)
def test_invalid_inputs_repeat_the_question(
    chat: Chat,
    state_messages: list[str],
    invalid: str,
    expected_state: State,
    hint: str,
) -> None:
    chat.send("agendar")
    for message in state_messages:
        chat.send(message)
    chat.send(invalid)
    assert hint in chat.text
    assert chat.state is expected_state
    assert chat.result.source is Source.NOT_UNDERSTOOD


def test_unavailable_time_offers_the_free_ones(chat: Chat) -> None:
    chat.send("agendar cardio")
    chat.send("segunda")
    chat.send("12:30")  # almoço
    assert "12:30 não está disponível" in chat.text
    assert chat.state is State.BOOK_TIME
    assert "13:30" in chat.options


def test_other_day_from_time_step(chat: Chat) -> None:
    chat.send("agendar cardio")
    chat.send("segunda")
    chat.send("outro dia")
    assert chat.state is State.BOOK_DAY


def test_declining_confirmation(chat: Chat) -> None:
    chat.send("agendar cardio")
    chat.send("segunda")
    chat.send("10h")
    chat.send("Ana Lima")
    chat.send("não")
    assert "não agendei" in chat.text
    assert chat.state is State.MENU
    assert chat.appointments() == []


@pytest.mark.parametrize("word", ["cancelar", "quero desistir de cancelar", "desmarca"])
def test_cancel_word_during_booking_abandons_flow(chat: Chat, word: str) -> None:
    chat.send("agendar cardio")
    chat.send("segunda")
    chat.send(word)
    assert "deixei o agendamento de lado" in chat.text
    assert chat.state is State.MENU
    assert chat.conversation.context == {}


@pytest.mark.parametrize("word", ["menu", "início", "sair", "0"])
def test_menu_command_during_flow(chat: Chat, word: str) -> None:
    chat.send("agendar cardio")
    chat.send(word)
    assert chat.state is State.MENU
    assert chat.options[0] == "Agendar consulta"


def test_information_question_mid_flow_keeps_booking(chat: Chat) -> None:
    chat.send("agendar cardio")
    chat.send("segunda")
    chat.send("aceita convênio?")
    assert "Plano Alfa" in chat.text
    assert "Qual horário?" in chat.text  # repete a pergunta do passo
    assert chat.state is State.BOOK_TIME
    chat.send("10h")
    assert chat.state is State.BOOK_NAME


def test_hours_word_while_choosing_time_means_free_slots(chat: Chat) -> None:
    chat.send("agendar cardio")
    chat.send("segunda")
    chat.send("quais horários?")
    assert "Horário de funcionamento" not in chat.text
    assert chat.state is State.BOOK_TIME


def test_hours_word_outside_flow_means_business_hours(chat: Chat) -> None:
    chat.send("oi")
    chat.send("qual o horário?")
    assert "Horário de funcionamento" in chat.text


def test_question_at_confirmation_step(chat: Chat) -> None:
    chat.send("agendar cardio")
    chat.send("segunda")
    chat.send("10h")
    chat.send("Ana Lima")
    chat.send("tem estacionamento?")
    assert "estacionamento gratuito" in chat.text
    assert "Posso confirmar?" in chat.text
    chat.send("sim")
    assert len(chat.appointments()) == 1


def test_slot_taken_between_choice_and_confirmation(new_chat: Callable[[str], Chat]) -> None:
    first, second = new_chat("5501"), new_chat("5502")
    for c in (first, second):
        c.send("agendar cardio")
        c.send("segunda")
        c.send("10h")
        c.send("Fulano Tal")
    first.send("sim")
    second.send("sim")
    assert "acabou de ser reservado por outra pessoa" in second.text
    assert second.state is State.BOOK_TIME
    assert "10:00" not in second.options
    second.send("10:30")
    second.send("Fulano Tal")
    second.send("sim")
    assert "Consulta confirmada" in second.text


def test_patient_already_booked_at_same_time(chat: Chat) -> None:
    book_monday(chat, "10h")
    chat.send("agendar clínico")
    chat.send("segunda")
    chat.send("10h")
    chat.send("Maria Silva")
    chat.send("sim")
    assert "Você já tem uma consulta confirmada nesse mesmo horário" in chat.text
    assert chat.state is State.BOOK_TIME


def test_slot_became_too_soon_before_confirmation(chat: Chat, clock: FixedClock) -> None:
    chat.send("agendar cardio")
    chat.send("hoje")
    chat.send("11h")
    chat.send("Ana Lima")
    clock.advance(timedelta(minutes=10))  # 11:00 deixa de respeitar a antecedência
    chat.send("sim")
    assert "não está mais disponível" in chat.text
    assert "11:00" not in chat.options


def test_no_availability_at_all(chat: Chat, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Scheduler, "available_days", lambda *a, **k: [])
    chat.send("agendar cardio")
    assert "Não há horários livres de Cardiologia nos próximos 14 dias" in chat.text
    assert chat.state is State.MENU


# --- consultar e cancelar ----------------------------------------------------------------


def test_my_appointments(chat: Chat) -> None:
    chat.send("oi")
    chat.send("2")
    assert "não tem consultas agendadas" in chat.text
    book_monday(chat, "10h")
    chat.send("minhas consultas")
    assert "Sua próxima consulta" in chat.text
    assert "segunda-feira, 16/11 às 10:00" in chat.text


def test_cancel_single_appointment(chat: Chat) -> None:
    book_monday(chat)
    chat.send("quero cancelar minha consulta")
    assert chat.state is State.CANCEL_CONFIRM
    assert "Cancelar esta consulta?" in chat.text
    chat.send("sim")
    assert "Consulta cancelada" in chat.text
    assert chat.appointments()[0].status == AppointmentStatus.CANCELLED
    assert chat.state is State.MENU


def test_cancel_word_confirms_in_cancel_flow(chat: Chat) -> None:
    book_monday(chat)
    chat.send("cancelar")
    chat.send("pode cancelar")
    assert chat.appointments()[0].status == AppointmentStatus.CANCELLED


def test_cancel_with_several_appointments(chat: Chat) -> None:
    book_monday(chat, "10h")
    book_monday(chat, "15h")
    chat.send("3")
    assert chat.state is State.CANCEL_PICK
    assert len(chat.options) == 2
    chat.send("5")
    assert "número da consulta" in chat.text
    chat.send("2")
    assert "15:00" in chat.text
    chat.send("sim")
    statuses = [a.status for a in chat.appointments()]
    assert statuses == [AppointmentStatus.CONFIRMED, AppointmentStatus.CANCELLED]


def test_keep_appointment(chat: Chat) -> None:
    book_monday(chat)
    chat.send("cancelar")
    chat.send("talvez")
    assert chat.state is State.CANCEL_CONFIRM
    chat.send("não")
    assert "continua marcada" in chat.text
    assert chat.appointments()[0].status == AppointmentStatus.CONFIRMED


def test_cancel_without_appointments(chat: Chat) -> None:
    chat.send("cancelar")
    assert "não tem consultas agendadas" in chat.text
    assert chat.state is State.MENU


def test_cancel_appointment_already_gone(chat: Chat) -> None:
    book_monday(chat)
    chat.send("cancelar")
    appointment = chat.appointments()[0]
    other = Chat(chat.engine, chat.session_factory, chat.clock, chat.contact)
    with chat.session_factory.begin() as session:
        chat.engine.scheduler.cancel(
            session, appointment.id, chat.contact, channel=Channel.SIMULATOR
        )
    other.send("sim")
    assert "Não encontrei essa consulta" in other.text


def test_cancel_pick_when_appointments_vanished(chat: Chat) -> None:
    book_monday(chat, "10h")
    book_monday(chat, "15h")
    chat.send("cancelar")
    with chat.session_factory.begin() as session:  # canceladas por outro canal
        for appointment in chat.appointments():
            chat.engine.scheduler.cancel(
                session, appointment.id, chat.contact, channel=Channel.SIMULATOR
            )
    chat.send("1")
    assert "não tem consultas agendadas" in chat.text


def test_cancelling_only_own_appointments(new_chat: Callable[[str], Chat]) -> None:
    owner, stranger = new_chat("5501"), new_chat("5502")
    book_monday(owner)
    stranger.send("cancelar")
    assert "não tem consultas agendadas" in stranger.text


# --- atendimento humano e urgência -------------------------------------------------------


def test_human_handoff_silences_bot(chat: Chat) -> None:
    chat.send("agendar cardio")
    chat.send("quero falar com um atendente")
    assert "Chamei alguém da nossa equipe" in chat.text
    conversation = chat.conversation
    assert conversation.status == ConversationStatus.WAITING_HUMAN
    assert conversation.priority == Priority.NORMAL
    assert conversation.state == State.MENU

    chat.send("alô?")
    assert chat.result.replies == []
    assert chat.result.source is Source.SILENT


def test_menu_returns_to_bot_before_agent_assigns(chat: Chat) -> None:
    chat.send("7")
    assert chat.conversation.status == ConversationStatus.WAITING_HUMAN
    chat.send("menu")
    assert "voltei a atender" in chat.text
    assert chat.conversation.status == ConversationStatus.BOT


def test_menu_does_not_return_when_agent_is_talking(chat: Chat) -> None:
    chat.send("atendente")
    with chat.session_factory.begin() as session:
        conversation = chat._conversation(session)
        conversation.status = ConversationStatus.HUMAN
    chat.send("menu")
    assert chat.result.replies == []
    assert chat.conversation.status == ConversationStatus.HUMAN


def test_urgency_has_priority_and_samu_guidance(chat: Chat) -> None:
    chat.send("agendar cardio")
    chat.send("é urgente, estou com dor no peito")
    assert "192 (SAMU)" in chat.text
    conversation = chat.conversation
    assert conversation.status == ConversationStatus.WAITING_HUMAN
    assert conversation.priority == Priority.URGENT


def test_urgency_while_waiting_raises_priority(chat: Chat) -> None:
    chat.send("atendente")
    chat.send("emergência!")
    assert "192" in chat.text
    assert chat.conversation.priority == Priority.URGENT


def test_negated_urgency_is_not_escalated(chat: Chat) -> None:
    chat.send("oi")
    chat.send("não é urgente, quero agendar")
    assert chat.conversation.status == ConversationStatus.BOT
    assert chat.state is State.BOOK_SPECIALTY


# --- expiração ---------------------------------------------------------------------------


def test_stale_flow_expires(chat: Chat, clock: FixedClock) -> None:
    chat.send("agendar cardio")
    chat.send("segunda")
    clock.advance(timedelta(minutes=31))
    chat.send("10h")
    assert "Retomando do início" in chat.text
    assert chat.state is State.MENU


def test_flow_within_30_minutes_continues(chat: Chat, clock: FixedClock) -> None:
    chat.send("agendar cardio")
    chat.send("segunda")
    clock.advance(timedelta(minutes=29))
    chat.send("10h")
    assert chat.state is State.BOOK_NAME


def test_specialty_removed_mid_booking_restarts_flow(chat: Chat) -> None:
    """A clínica tira a especialidade do clinica.yaml e reinicia: quem estava no meio do
    agendamento recebe aviso e menu, em vez de silêncio."""
    from sqlalchemy import update

    from app.db.models import Specialty

    chat.send("quero agendar cardiologia")
    with chat.session_factory.begin() as session:
        session.execute(
            update(Specialty).where(Specialty.slug == "cardiologia").values(active=False)
        )
    chat.send("segunda")
    assert "não está mais disponível" in chat.text
    assert chat.state == State.MENU
