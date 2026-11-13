import threading
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.clock import FixedClock
from app.db.models import Appointment, AppointmentStatus, Channel
from app.scheduling import (
    AppointmentNotFound,
    InvalidSlot,
    OutsideBusinessHours,
    PatientAlreadyBooked,
    Scheduler,
    SlotTaken,
    SpecialtyNotFound,
    TooFarAhead,
    TooSoon,
)
from app.scheduling.errors import translate_integrity_error

THURSDAY = date(2026, 11, 12)
MONDAY = date(2026, 11, 16)
PHONE = "5500000000001"


def book(
    scheduler: Scheduler,
    session: Session,
    starts_at: datetime,
    *,
    phone: str = PHONE,
    slug: str = "cardiologia",
    channel: Channel = Channel.SIMULATOR,
) -> Appointment:
    return scheduler.book(
        session,
        specialty=scheduler.get_specialty(session, slug),
        starts_at=starts_at,
        patient_phone=phone,
        patient_name="Paciente Teste",
        channel=channel,
    )


def at(day: date, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day.isoformat()} {hhmm}")


def test_unknown_specialty(scheduler: Scheduler, session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session, pytest.raises(SpecialtyNotFound):
        scheduler.get_specialty(session, "ortopedia")


def test_slots_follow_business_hours(
    scheduler: Scheduler, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        spec = scheduler.get_specialty(session, "cardiologia")
        slots = scheduler.available_slots(session, spec, MONDAY)
    times = [f"{s:%H:%M}" for s in slots]
    assert times[:3] == ["08:00", "08:30", "09:00"]
    assert "11:30" in times
    assert "12:00" not in times  # almoço
    assert "13:00" not in times
    assert times[-1] == "17:30"
    assert len(times) == 8 + 9


def test_slots_today_respect_min_advance(
    scheduler: Scheduler, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        spec = scheduler.get_specialty(session, "cardiologia")
        slots = scheduler.available_slots(session, spec, THURSDAY)
    assert slots[0] == at(THURSDAY, "11:00")  # agora 09:00 + 120 min


@pytest.mark.parametrize(
    "day",
    [date(2026, 11, 15), date(2026, 11, 11), THURSDAY + timedelta(days=15)],
    ids=["domingo", "passado", "alem-da-janela"],
)
def test_no_slots(scheduler: Scheduler, session_factory: sessionmaker[Session], day: date) -> None:
    with session_factory() as session:
        spec = scheduler.get_specialty(session, "cardiologia")
        assert scheduler.available_slots(session, spec, day) == []


def test_booked_slot_disappears_only_for_that_specialty(
    scheduler: Scheduler, session_factory: sessionmaker[Session]
) -> None:
    with session_factory.begin() as session:
        book(scheduler, session, at(MONDAY, "10:00"))
    with session_factory() as session:
        cardio = scheduler.get_specialty(session, "cardiologia")
        general = scheduler.get_specialty(session, "clinica-geral")
        assert at(MONDAY, "10:00") not in scheduler.available_slots(session, cardio, MONDAY)
        assert at(MONDAY, "10:00") in scheduler.available_slots(session, general, MONDAY)


def test_available_days(scheduler: Scheduler, session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        spec = scheduler.get_specialty(session, "cardiologia")
        days = scheduler.available_days(session, spec)
        first_three = scheduler.available_days(session, spec, limit=3)
    assert date(2026, 11, 15) not in days  # domingo
    assert days[0] == THURSDAY
    assert days[-1] == THURSDAY + timedelta(days=14)
    assert first_three == [THURSDAY, date(2026, 11, 13), date(2026, 11, 14)]


def test_fully_booked_day_is_not_offered(
    scheduler: Scheduler, session_factory: sessionmaker[Session], clock: FixedClock
) -> None:
    clock.set(datetime(2026, 11, 13, 9, 0, tzinfo=clock.tz))  # sexta
    saturday = date(2026, 11, 14)
    with session_factory.begin() as session:
        for i, hhmm in enumerate(["08:00", "08:30", "09:00", "09:30", "10:00", "10:30"]):
            book(scheduler, session, at(saturday, hhmm), phone=f"55000000000{i:02d}")
        for i, hhmm in enumerate(["11:00", "11:30"]):
            book(scheduler, session, at(saturday, hhmm), phone=f"55000000001{i:02d}")
    with session_factory() as session:
        spec = scheduler.get_specialty(session, "cardiologia")
        assert saturday not in scheduler.available_days(session, spec)


def test_book_and_list_upcoming(
    scheduler: Scheduler, session_factory: sessionmaker[Session]
) -> None:
    with session_factory.begin() as session:
        later = book(scheduler, session, at(MONDAY, "15:00"))
        sooner = book(scheduler, session, at(MONDAY, "09:00"), slug="clinica-geral")
        book(scheduler, session, at(MONDAY, "10:00"), phone="5500000000999")
    with session_factory() as session:
        upcoming = scheduler.list_upcoming(session, PHONE, channel=Channel.SIMULATOR)
    assert [a.id for a in upcoming] == [sooner.id, later.id]
    assert upcoming[1].specialty.name == "Cardiologia"
    assert upcoming[1].created_at == datetime(2026, 11, 12, 9, 0)


@pytest.mark.parametrize(
    ("starts_at", "error"),
    [
        (at(MONDAY, "10:15"), InvalidSlot),
        (at(date(2026, 11, 15), "10:00"), OutsideBusinessHours),
        (at(MONDAY, "12:30"), OutsideBusinessHours),
        (at(THURSDAY, "10:00"), TooSoon),
        (at(THURSDAY + timedelta(days=15), "10:00"), TooFarAhead),
    ],
)
def test_database_rejections_become_domain_errors(
    scheduler: Scheduler,
    session_factory: sessionmaker[Session],
    starts_at: datetime,
    error: type[Exception],
) -> None:
    with session_factory() as session, pytest.raises(error):
        book(scheduler, session, starts_at)


def test_slot_taken_keeps_outer_transaction_usable(
    scheduler: Scheduler, session_factory: sessionmaker[Session]
) -> None:
    with session_factory.begin() as session:
        book(scheduler, session, at(MONDAY, "10:00"), phone="5500000000002")
    with session_factory.begin() as session:
        with pytest.raises(SlotTaken):
            book(scheduler, session, at(MONDAY, "10:00"))
        book(scheduler, session, at(MONDAY, "10:30"))  # mesma transação segue válida
    with session_factory() as session:
        assert len(scheduler.list_upcoming(session, PHONE, channel=Channel.SIMULATOR)) == 1


def test_patient_double_booking(
    scheduler: Scheduler, session_factory: sessionmaker[Session]
) -> None:
    with session_factory.begin() as session:
        book(scheduler, session, at(MONDAY, "10:00"), slug="cardiologia")
        with pytest.raises(PatientAlreadyBooked):
            book(scheduler, session, at(MONDAY, "10:00"), slug="clinica-geral")


def test_channels_do_not_see_each_others_appointments(
    scheduler: Scheduler, session_factory: sessionmaker[Session]
) -> None:
    """O simulador é público e aceita qualquer número: não pode listar, cancelar nem esbarrar
    nas consultas de um paciente do WhatsApp com o mesmo número."""
    with session_factory.begin() as session:
        whatsapp = book(scheduler, session, at(MONDAY, "10:00"), channel=Channel.WHATSAPP)
        # mesmo número, mesmo horário, outro canal: não é o mesmo paciente
        book(scheduler, session, at(MONDAY, "10:00"), slug="clinica-geral")
    with session_factory.begin() as session:
        simulator = scheduler.list_upcoming(session, PHONE, channel=Channel.SIMULATOR)
        assert [a.channel for a in simulator] == [Channel.SIMULATOR]
        with pytest.raises(AppointmentNotFound):
            scheduler.cancel(session, whatsapp.id, PHONE, channel=Channel.SIMULATOR)
    with session_factory() as session:
        assert [
            a.id for a in scheduler.list_upcoming(session, PHONE, channel=Channel.WHATSAPP)
        ] == [whatsapp.id]


def test_race_for_the_same_slot(
    scheduler: Scheduler, session_factory: sessionmaker[Session]
) -> None:
    """Dois clientes confirmam o mesmo horário ao mesmo tempo: só um consegue."""
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def attempt(phone: str) -> None:
        with session_factory() as session:
            spec = scheduler.get_specialty(session, "cardiologia")
            assert at(MONDAY, "10:00") in scheduler.available_slots(session, spec, MONDAY)
            session.commit()  # ambos viram o horário livre
            barrier.wait()
            try:
                book(scheduler, session, at(MONDAY, "10:00"), phone=phone)
                session.commit()
                results[phone] = "ok"
            except SlotTaken as exc:
                session.rollback()
                results[phone] = exc

    threads = [threading.Thread(target=attempt, args=(p,)) for p in ("5501", "5502")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    outcomes = list(results.values())
    assert outcomes.count("ok") == 1
    assert sum(isinstance(o, SlotTaken) for o in outcomes) == 1


def test_cancel_frees_slot(scheduler: Scheduler, session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        appointment = book(scheduler, session, at(MONDAY, "10:00"))
    with session_factory.begin() as session:
        cancelled = scheduler.cancel(session, appointment.id, PHONE, channel=Channel.SIMULATOR)
        assert cancelled.status == AppointmentStatus.CANCELLED
        assert cancelled.cancelled_at == datetime(2026, 11, 12, 9, 0)
    with session_factory.begin() as session:
        book(scheduler, session, at(MONDAY, "10:00"), phone="5500000000002")


def test_cannot_cancel_someone_elses_or_twice(
    scheduler: Scheduler, session_factory: sessionmaker[Session]
) -> None:
    with session_factory.begin() as session:
        appointment = book(scheduler, session, at(MONDAY, "10:00"))
    with session_factory.begin() as session, pytest.raises(AppointmentNotFound):
        scheduler.cancel(session, appointment.id, "5599999999999", channel=Channel.SIMULATOR)
    with session_factory.begin() as session:
        scheduler.cancel(session, appointment.id, PHONE, channel=Channel.SIMULATOR)
    with session_factory.begin() as session, pytest.raises(AppointmentNotFound):
        scheduler.cancel(session, appointment.id, PHONE, channel=Channel.SIMULATOR)


def test_past_appointments_are_not_upcoming_nor_cancellable(
    scheduler: Scheduler, session_factory: sessionmaker[Session], clock: FixedClock
) -> None:
    with session_factory.begin() as session:
        appointment = book(scheduler, session, at(MONDAY, "10:00"))
    clock.advance(timedelta(days=5))
    with session_factory() as session:
        assert scheduler.list_upcoming(session, PHONE, channel=Channel.SIMULATOR) == []
        with pytest.raises(AppointmentNotFound):
            scheduler.cancel(session, appointment.id, PHONE, channel=Channel.SIMULATOR)


def test_unknown_integrity_error_is_not_translated(db_engine: Engine) -> None:
    error = IntegrityError("x", {}, Exception("NOT NULL constraint failed: users.name"))
    assert translate_integrity_error(error) is None


def test_unknown_integrity_error_propagates(
    scheduler: Scheduler, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session, pytest.raises(IntegrityError):
        scheduler.book(
            session,
            specialty=scheduler.get_specialty(session, "cardiologia"),
            starts_at=at(MONDAY, "10:00"),
            patient_phone="",
            patient_name="Paciente Teste",
            channel=Channel.SIMULATOR,
        )
