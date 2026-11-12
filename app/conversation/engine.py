"""Motor de conversa: regex + máquina de estados.

Ordem de avaliação de cada mensagem:
1. status da conversa: com humano, o bot fica em silêncio;
2. expiração do fluxo parado;
3. comandos globais (urgência, atendente, menu);
4. dentro de um fluxo: desistência, perguntas informativas (responde e repete a pergunta do
   passo) e o passo atual;
5. fora de fluxo: opção do menu, intenções de ação, FAQ, intenções informativas, saudação,
   IA de reserva (se ligada); por fim, "não entendi" (na 3ª seguida, oferece atendente).

O motor altera a `Conversation` recebida; quem chama controla a transação.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from app.ai.responder import AIResponder, AIStatus
from app.clinic import ClinicData, SpecialtyInfo
from app.clock import Clock
from app.conversation import texts
from app.conversation.reply import Reply
from app.conversation.states import BOOKING_STATES, State
from app.db.models import (
    Appointment,
    Channel,
    Conversation,
    ConversationStatus,
    Priority,
    Specialty,
)
from app.nlu import intents
from app.nlu.intents import Intent
from app.nlu.normalize import contains_phrase, normalize
from app.nlu.parsers import parse_date, parse_name, parse_option, parse_time, parse_yes_no
from app.scheduling import (
    AppointmentNotFound,
    PatientAlreadyBooked,
    Scheduler,
    SchedulingError,
    SlotTaken,
    SpecialtyNotFound,
)

FLOW_EXPIRATION = timedelta(minutes=30)
MAX_DAY_OPTIONS = 7
# Botões de sim/não enviam o próprio texto (o texto da pergunta já pede "sim" ou "não").
YES_NO = ("Sim", "Não")
UNKNOWN_BEFORE_OFFER = 3

MENU_ACTIONS = (
    Intent.BOOK,
    Intent.MY_APPOINTMENTS,
    Intent.CANCEL,
    Intent.PRICES,
    Intent.INSURANCE,
    Intent.ADDRESS,
    Intent.HUMAN,
)


class Source(StrEnum):
    """Origem da resposta (painel "por dentro do bot")."""

    RULE = "rule"
    FAQ = "faq"
    AI = "ai"
    NOT_UNDERSTOOD = "not_understood"
    SILENT = "silent"


@dataclass
class EngineResult:
    replies: list[Reply]
    intent: str | None = None
    source: Source = Source.RULE


@dataclass
class _Turn:
    session: Session
    conversation: Conversation
    raw: str
    text: str  # normalizado
    ctx: dict[str, Any] = field(default_factory=dict)

    @property
    def state(self) -> State:
        return State(self.conversation.state)

    def go(self, state: State) -> None:
        self.conversation.state = state


class ConversationEngine:
    def __init__(
        self,
        clinic: ClinicData,
        scheduler: Scheduler,
        clock: Clock,
        ai: AIResponder | None = None,
    ) -> None:
        self.clinic = clinic
        self.scheduler = scheduler
        self.clock = clock
        self.ai = ai

    # --- entrada --------------------------------------------------------------------------

    def handle(self, session: Session, conversation: Conversation, text: str) -> EngineResult:
        now = self.clock.local_now()
        previous_inbound = conversation.last_inbound_at
        conversation.last_inbound_at = now
        turn = _Turn(session, conversation, text.strip(), normalize(text))
        turn.ctx = dict(conversation.context or {})

        if conversation.status != ConversationStatus.BOT:
            result = self._while_with_human(turn)
        else:
            expired = (
                turn.state is not State.MENU
                and previous_inbound is not None
                and now - previous_inbound > FLOW_EXPIRATION
            )
            if expired:
                self._reset(turn)
            result = self._dispatch(turn, is_new=previous_inbound is None)
            if expired:
                result.replies.insert(0, Reply(texts.EXPIRED))
            self._track_understanding(turn, result)

        conversation.context = turn.ctx
        return result

    def _while_with_human(self, turn: _Turn) -> EngineResult:
        conversation = turn.conversation
        waiting = conversation.status == ConversationStatus.WAITING_HUMAN
        if intents.matches(Intent.URGENT, turn.text):
            conversation.priority = Priority.URGENT
            # Orientação de emergência sempre sai, mesmo com humano na conversa.
            return EngineResult([Reply(texts.HANDOFF_URGENT)], Intent.URGENT)
        if waiting and intents.matches(Intent.MENU, turn.text):
            conversation.status = ConversationStatus.BOT
            conversation.priority = Priority.NORMAL
            conversation.handoff_reason = None
            self._reset(turn)
            return EngineResult([Reply(texts.BACK_FROM_HANDOFF), self._menu()], Intent.MENU)
        return EngineResult([], source=Source.SILENT)

    def _track_understanding(self, turn: _Turn, result: EngineResult) -> None:
        conversation = turn.conversation
        if result.source is not Source.NOT_UNDERSTOOD:
            conversation.unknown_count = 0
            return
        conversation.unknown_count += 1
        if conversation.unknown_count >= UNKNOWN_BEFORE_OFFER:
            result.replies.append(Reply(texts.OFFER_HUMAN))

    def _dispatch(self, turn: _Turn, *, is_new: bool) -> EngineResult:
        if intents.matches(Intent.URGENT, turn.text):
            return self._handoff(turn, urgent=True)
        if intents.matches(Intent.HUMAN, turn.text):
            return self._handoff(turn, urgent=False)
        if intents.matches(Intent.MENU, turn.text):
            self._reset(turn)
            return EngineResult([self._menu()], Intent.MENU)

        state = turn.state
        if state is State.MENU:
            return self._out_of_flow(turn, is_new=is_new)

        if state in BOOKING_STATES and intents.matches(Intent.CANCEL, turn.text):
            self._reset(turn)
            return EngineResult([Reply(texts.FLOW_ABORTED), self._menu()], Intent.CANCEL)

        info = self._info(turn, ambiguous=self._ambiguous_info(state))
        if info is not None:
            info.replies.append(self._prompt(turn))
            return info

        return self._step(turn)

    # --- fora de fluxo --------------------------------------------------------------------

    def _out_of_flow(self, turn: _Turn, *, is_new: bool) -> EngineResult:
        option = parse_option(turn.text, len(MENU_ACTIONS))
        intent = MENU_ACTIONS[option - 1] if option else None
        intent = intent or intents.detect(turn.text, intents.ACTION_INTENTS)

        if intent is Intent.HUMAN:
            return self._handoff(turn, urgent=False)
        if intent is Intent.BOOK:
            return self._start_booking(turn)
        if intent is Intent.MY_APPOINTMENTS:
            return self._my_appointments(turn)
        if intent is Intent.CANCEL:
            return self._start_cancel(turn)
        if intent is Intent.ADDRESS and option:
            text = f"{texts.address(self.clinic)}\n\n{texts.business_hours(self.clinic)}"
            return EngineResult([Reply(text)], intent)
        if intent is not None:
            return self._answer_info(intent)

        info = self._info(turn)
        if info is not None:
            return info

        social = intents.detect(turn.text, intents.SOCIAL_INTENTS)
        if social is Intent.THANKS:
            return EngineResult([Reply(texts.THANKS)], social)
        if social is Intent.GREETING:
            return EngineResult([self._menu(texts.greeting(self.clinic))], Intent.GREETING)

        if self.ai is not None:
            ai_result = self.ai.answer(turn.session, turn.conversation, turn.raw)
            if ai_result.status is AIStatus.ANSWERED and ai_result.text:
                return EngineResult([Reply(ai_result.text)], "ai", Source.AI)
            if ai_result.status is AIStatus.HANDOFF:
                return self._handoff(turn, urgent=False, reason=texts.AI_HANDOFF_REASON)
            # Erro, limite ou pergunta longa: segue como se a IA estivesse desligada.

        if is_new:
            return EngineResult([self._menu(texts.greeting(self.clinic))], Intent.GREETING)

        return EngineResult([self._menu(texts.NOT_UNDERSTOOD)], source=Source.NOT_UNDERSTOOD)

    def _menu(self, text: str = texts.MENU_PROMPT) -> Reply:
        return Reply(text, texts.MENU_OPTIONS)

    def _handoff(self, turn: _Turn, *, urgent: bool, reason: str | None = None) -> EngineResult:
        conversation = turn.conversation
        conversation.status = ConversationStatus.WAITING_HUMAN
        conversation.priority = Priority.URGENT if urgent else Priority.NORMAL
        conversation.handoff_reason = reason or (
            "Urgência informada pelo cliente" if urgent else "Cliente pediu atendente"
        )
        self._reset(turn)
        if urgent:
            return EngineResult([Reply(texts.HANDOFF_URGENT)], Intent.URGENT)
        if reason == texts.AI_HANDOFF_REASON:
            return EngineResult([Reply(texts.AI_HANDOFF)], "ai_handoff", Source.AI)
        return EngineResult([Reply(texts.HANDOFF)], Intent.HUMAN)

    def _reset(self, turn: _Turn) -> None:
        turn.go(State.MENU)
        turn.ctx = {}

    # --- informações (FAQ antes das intenções genéricas, por ser mais específico) ---------

    @staticmethod
    def _ambiguous_info(state: State) -> tuple[Intent, ...]:
        # Durante a escolha de dia/hora, "horário" significa horários livres, não funcionamento.
        if state in (State.BOOK_DAY, State.BOOK_TIME):
            return (Intent.HOURS,)
        return ()

    def _info(self, turn: _Turn, ambiguous: tuple[Intent, ...] = ()) -> EngineResult | None:
        for entry in self.clinic.faq:
            if any(contains_phrase(turn.text, term) for term in entry.match_terms):
                return EngineResult([Reply(entry.answer)], "faq", Source.FAQ)
        candidates = tuple(i for i in intents.INFO_INTENTS if i not in ambiguous)
        intent = intents.detect(turn.text, candidates)
        return self._answer_info(intent) if intent else None

    def _answer_info(self, intent: Intent) -> EngineResult:
        answers: dict[Intent, Callable[[ClinicData], str]] = {
            Intent.ADDRESS: texts.address,
            Intent.HOURS: texts.business_hours,
            Intent.INSURANCE: texts.insurances,
            Intent.PRICES: texts.specialties_and_prices,
            Intent.SPECIALTIES: texts.specialties_and_prices,
        }
        return EngineResult([Reply(answers[intent](self.clinic))], intent)

    # --- passos do fluxo ------------------------------------------------------------------

    def _step(self, turn: _Turn) -> EngineResult:
        handlers: dict[State, Callable[[_Turn], EngineResult]] = {
            State.BOOK_SPECIALTY: self._on_specialty,
            State.BOOK_DAY: self._on_day,
            State.BOOK_TIME: self._on_time,
            State.BOOK_NAME: self._on_name,
            State.BOOK_CONFIRM: self._on_confirm,
            State.CANCEL_PICK: self._on_cancel_pick,
            State.CANCEL_CONFIRM: self._on_cancel_confirm,
        }
        return handlers[turn.state](turn)

    def _prompt(self, turn: _Turn) -> Reply:
        """Repete a pergunta do passo atual (após uma resposta informativa)."""
        state = turn.state
        if state is State.BOOK_SPECIALTY:
            return self._ask_specialty()
        if state is State.BOOK_DAY:
            return self._day_reply(turn, self._offered_days(turn))
        if state is State.BOOK_TIME:
            return self._time_reply(turn, self._ctx_day(turn), self._slots(turn))
        if state is State.BOOK_NAME:
            return Reply(texts.ASK_NAME)
        if state is State.BOOK_CONFIRM:
            return self._confirm_reply(turn)
        if state is State.CANCEL_PICK:
            return self._cancel_pick_reply(self._cancel_candidates(turn))
        return Reply(texts.confirm_cancel(self._cancel_target(turn)))

    def _retry(self, turn: _Turn, message: str) -> EngineResult:
        return EngineResult([Reply(message), self._prompt(turn)], source=Source.NOT_UNDERSTOOD)

    # --- agendamento ----------------------------------------------------------------------

    def _match_specialty(self, text: str) -> SpecialtyInfo | None:
        best: tuple[int, SpecialtyInfo] | None = None
        for spec in self.clinic.specialties:
            for term in spec.match_terms:
                if contains_phrase(text, term) and (best is None or len(term) > best[0]):
                    best = (len(term), spec)
        return best[1] if best else None

    def _start_booking(self, turn: _Turn) -> EngineResult:
        turn.ctx = {}
        spec = self._match_specialty(turn.text)
        if spec is not None:
            return self._choose_specialty(turn, spec)
        turn.go(State.BOOK_SPECIALTY)
        return EngineResult([self._ask_specialty()], Intent.BOOK)

    def _ask_specialty(self) -> Reply:
        return Reply(texts.ASK_SPECIALTY, tuple(s.name for s in self.clinic.specialties))

    def _on_specialty(self, turn: _Turn) -> EngineResult:
        option = parse_option(turn.text, len(self.clinic.specialties))
        spec = self.clinic.specialties[option - 1] if option else self._match_specialty(turn.text)
        if spec is None:
            return self._retry(turn, texts.INVALID_SPECIALTY)
        return self._choose_specialty(turn, spec)

    def _choose_specialty(self, turn: _Turn, spec: SpecialtyInfo) -> EngineResult:
        turn.ctx["specialty"] = spec.slug
        return self._ask_day(turn)

    def _spec(self, turn: _Turn) -> tuple[SpecialtyInfo, Specialty]:
        slug = turn.ctx["specialty"]
        info = self.clinic.specialty(slug)
        if info is None:
            raise SpecialtyNotFound(slug)
        return info, self.scheduler.get_specialty(turn.session, slug)

    def _ask_day(self, turn: _Turn, prefix: list[Reply] | None = None) -> EngineResult:
        info, row = self._spec(turn)
        days = self.scheduler.available_days(turn.session, row, limit=MAX_DAY_OPTIONS)
        replies = list(prefix or [])
        if not days:
            self._reset(turn)
            replies.append(Reply(texts.no_days(info, self.clinic.scheduling.days_ahead)))
            return EngineResult(replies, Intent.BOOK)
        turn.ctx["offered_days"] = [d.isoformat() for d in days]
        turn.ctx.pop("day", None)
        turn.go(State.BOOK_DAY)
        replies.append(self._day_reply(turn, days))
        return EngineResult(replies, Intent.BOOK)

    def _offered_days(self, turn: _Turn) -> list[date]:
        return [date.fromisoformat(d) for d in turn.ctx.get("offered_days", [])]

    def _day_reply(self, turn: _Turn, days: list[date]) -> Reply:
        info, _ = self._spec(turn)
        return Reply(texts.ask_day(info), tuple(texts.fmt_day_option(d) for d in days))

    def _on_day(self, turn: _Turn) -> EngineResult:
        offered = self._offered_days(turn)
        option = parse_option(turn.text, len(offered))
        today = self.clock.today()
        day = offered[option - 1] if option else parse_date(turn.text, today)
        if day is None:
            return self._retry(turn, texts.INVALID_DAY)

        _, row = self._spec(turn)
        slots = self.scheduler.available_slots(turn.session, row, day)
        if not slots:
            return self._retry(turn, texts.day_unavailable(day, self._why_no_slots(day)))
        turn.ctx["day"] = day.isoformat()
        turn.go(State.BOOK_TIME)
        return EngineResult([self._time_reply(turn, day, slots)], Intent.BOOK)

    def _why_no_slots(self, day: date) -> str:
        if day < self.clock.today():
            return texts.REASON_PAST
        if day > self.scheduler.last_bookable_day:
            return texts.REASON_TOO_FAR
        if not self.scheduler.is_open(day):
            return texts.REASON_CLOSED
        return texts.REASON_FULL

    def _ctx_day(self, turn: _Turn) -> date:
        return date.fromisoformat(turn.ctx["day"])

    def _slots(self, turn: _Turn) -> list[datetime]:
        _, row = self._spec(turn)
        return self.scheduler.available_slots(turn.session, row, self._ctx_day(turn))

    def _time_reply(self, turn: _Turn, day: date, slots: list[datetime]) -> Reply:
        info, _ = self._spec(turn)
        return Reply(
            texts.ask_time(info, day, slots),
            tuple(texts.fmt_time(s) for s in slots),
            numbered=False,
        )

    def _on_time(self, turn: _Turn) -> EngineResult:
        if contains_phrase(turn.text, "outro dia") or contains_phrase(turn.text, "outra data"):
            return self._ask_day(turn)
        value = parse_time(turn.text)
        if value is None:
            return self._retry(turn, texts.INVALID_TIME)
        starts_at = datetime.combine(self._ctx_day(turn), value)
        slots = self._slots(turn)
        if starts_at not in slots:
            return self._slot_problem(turn, texts.time_unavailable(value), slots)
        turn.ctx["starts_at"] = starts_at.isoformat()
        turn.go(State.BOOK_NAME)
        return EngineResult([Reply(texts.ASK_NAME)], Intent.BOOK)

    def _slot_problem(
        self, turn: _Turn, message: str, slots: list[datetime] | None = None
    ) -> EngineResult:
        """Horário indisponível: oferece outros do mesmo dia, ou outros dias."""
        slots = self._slots(turn) if slots is None else slots
        if not slots:
            return self._ask_day(turn, prefix=[Reply(message)])
        turn.ctx.pop("starts_at", None)
        turn.go(State.BOOK_TIME)
        return EngineResult(
            [Reply(message), self._time_reply(turn, self._ctx_day(turn), slots)],
            Intent.BOOK,
        )

    def _on_name(self, turn: _Turn) -> EngineResult:
        name = parse_name(turn.raw)
        if name is None:
            return EngineResult([Reply(texts.INVALID_NAME)], source=Source.NOT_UNDERSTOOD)
        turn.ctx["patient_name"] = name
        turn.go(State.BOOK_CONFIRM)
        return EngineResult([self._confirm_reply(turn)], Intent.BOOK)

    def _confirm_reply(self, turn: _Turn) -> Reply:
        info, _ = self._spec(turn)
        starts_at = datetime.fromisoformat(turn.ctx["starts_at"])
        return Reply(
            texts.confirm_booking(info, starts_at, turn.ctx["patient_name"]), YES_NO, numbered=False
        )

    def _on_confirm(self, turn: _Turn) -> EngineResult:
        answer = parse_yes_no(turn.text)
        if answer is None:
            return self._retry(turn, texts.CONFIRM_YES_NO)
        if not answer:
            self._reset(turn)
            return EngineResult([Reply(texts.BOOKING_DECLINED)], Intent.BOOK)

        _, row = self._spec(turn)
        try:
            appointment = self.scheduler.book(
                turn.session,
                specialty=row,
                starts_at=datetime.fromisoformat(turn.ctx["starts_at"]),
                patient_phone=turn.conversation.contact,
                patient_name=turn.ctx["patient_name"],
                channel=Channel(turn.conversation.channel),
            )
        except SlotTaken:
            return self._slot_problem(turn, texts.SLOT_TAKEN)
        except PatientAlreadyBooked:
            return self._slot_problem(turn, texts.PATIENT_BUSY)
        except SchedulingError:
            return self._slot_problem(turn, texts.SLOT_NO_LONGER_VALID)
        self._reset(turn)
        return EngineResult([Reply(texts.booked(appointment, self.clinic))], Intent.BOOK)

    # --- consultas e cancelamento ---------------------------------------------------------

    def _my_appointments(self, turn: _Turn) -> EngineResult:
        appointments = self.scheduler.list_upcoming(turn.session, turn.conversation.contact)
        text = texts.upcoming(appointments) if appointments else texts.NO_APPOINTMENTS
        return EngineResult([Reply(text)], Intent.MY_APPOINTMENTS)

    def _start_cancel(self, turn: _Turn) -> EngineResult:
        appointments = self.scheduler.list_upcoming(turn.session, turn.conversation.contact)
        if not appointments:
            return EngineResult([Reply(texts.NO_APPOINTMENTS)], Intent.CANCEL)
        if len(appointments) == 1:
            return self._ask_cancel_confirm(turn, appointments[0])
        turn.ctx = {"cancel_ids": [a.id for a in appointments]}
        turn.go(State.CANCEL_PICK)
        return EngineResult([self._cancel_pick_reply(appointments)], Intent.CANCEL)

    def _cancel_candidates(self, turn: _Turn) -> list[Appointment]:
        ids = turn.ctx.get("cancel_ids", [])
        by_id = {
            a.id: a for a in self.scheduler.list_upcoming(turn.session, turn.conversation.contact)
        }
        return [by_id[i] for i in ids if i in by_id]

    def _cancel_pick_reply(self, appointments: list[Appointment]) -> Reply:
        return Reply(
            texts.ASK_WHICH_TO_CANCEL,
            tuple(texts.fmt_appointment(a) for a in appointments),
        )

    def _on_cancel_pick(self, turn: _Turn) -> EngineResult:
        candidates = self._cancel_candidates(turn)
        if not candidates:
            self._reset(turn)
            return EngineResult([Reply(texts.NO_APPOINTMENTS)], Intent.CANCEL)
        option = parse_option(turn.text, len(candidates))
        if option is None:
            return self._retry(turn, texts.INVALID_CANCEL_OPTION)
        return self._ask_cancel_confirm(turn, candidates[option - 1])

    def _ask_cancel_confirm(self, turn: _Turn, appointment: Appointment) -> EngineResult:
        turn.ctx = {"appointment_id": appointment.id}
        turn.go(State.CANCEL_CONFIRM)
        return EngineResult(
            [Reply(texts.confirm_cancel(appointment), YES_NO, numbered=False)], Intent.CANCEL
        )

    def _cancel_target(self, turn: _Turn) -> Appointment:
        appointment = turn.session.get(Appointment, turn.ctx["appointment_id"])
        if appointment is None:
            raise AppointmentNotFound(str(turn.ctx["appointment_id"]))
        return appointment

    def _on_cancel_confirm(self, turn: _Turn) -> EngineResult:
        answer = parse_yes_no(turn.text)
        # Aqui "cancelar" é o objetivo do fluxo, não desistência: vale como "sim".
        if answer is None and intents.matches(Intent.CANCEL, turn.text):
            answer = True
        if answer is None:
            return self._retry(turn, texts.CONFIRM_YES_NO)
        appointment_id = turn.ctx["appointment_id"]
        self._reset(turn)
        if not answer:
            return EngineResult([Reply(texts.CANCEL_KEPT)], Intent.CANCEL)
        try:
            self.scheduler.cancel(turn.session, appointment_id, turn.conversation.contact)
        except AppointmentNotFound:
            return EngineResult([Reply(texts.CANCEL_NOT_FOUND)], Intent.CANCEL)
        return EngineResult([Reply(texts.CANCELLED)], Intent.CANCEL)
