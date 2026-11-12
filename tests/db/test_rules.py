"""Cada regra de agenda é testada direto no banco, sem passar pelo código da aplicação."""

from datetime import timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.clock import FixedClock

INSERT = text(
    "INSERT INTO appointments "
    "(specialty_id, patient_phone, patient_name, starts_at, channel, created_at) "
    "VALUES (:specialty, :phone, 'Paciente Teste', :starts_at, 'simulator', '2026-11-12 09:00:00')"
)
MONDAY = "2026-11-16"


def insert(
    engine: Engine, starts_at: str, *, specialty: int = 1, phone: str = "5500000000001"
) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            INSERT, {"specialty": specialty, "phone": phone, "starts_at": starts_at}
        )
        return int(result.lastrowid or 0)


def rejects(engine: Engine, starts_at: str, error: str, **kwargs: object) -> None:
    with pytest.raises(IntegrityError, match=error):
        insert(engine, starts_at, **kwargs)  # type: ignore[arg-type]


def test_valid_appointment_is_accepted(db_engine: Engine) -> None:
    assert insert(db_engine, f"{MONDAY} 10:00:00") > 0


@pytest.mark.parametrize(
    "starts_at",
    [f"{MONDAY} 10:15:00", f"{MONDAY} 10:00:30", f"{MONDAY} 10:45:00"],
)
def test_start_must_be_on_30_minute_grid(db_engine: Engine, starts_at: str) -> None:
    rejects(db_engine, starts_at, "ck_appointments_starts_at_slot")


@pytest.mark.parametrize("starts_at", [f"{MONDAY} 10:00", "16/11/2026 10:00", "amanhã"])
def test_start_must_use_canonical_format(db_engine: Engine, starts_at: str) -> None:
    # Formatos alternativos burlariam o índice único ('10:00' != '10:00:00').
    with pytest.raises(IntegrityError):
        insert(db_engine, starts_at)


def test_closed_on_sunday(db_engine: Engine) -> None:
    rejects(db_engine, "2026-11-15 10:00:00", "outside_business_hours")


@pytest.mark.parametrize(
    "starts_at",
    [
        f"{MONDAY} 07:30:00",  # antes de abrir
        f"{MONDAY} 12:00:00",  # almoço
        f"{MONDAY} 13:00:00",  # almoço
        f"{MONDAY} 18:00:00",  # começa no fechamento
        "2026-11-14 12:00:00",  # sábado à tarde
    ],
)
def test_outside_business_hours(db_engine: Engine, starts_at: str) -> None:
    rejects(db_engine, starts_at, "outside_business_hours")


@pytest.mark.parametrize(
    "starts_at",
    [f"{MONDAY} 08:00:00", f"{MONDAY} 11:30:00", f"{MONDAY} 13:30:00", f"{MONDAY} 17:30:00"],
)
def test_edges_of_business_hours_are_accepted(db_engine: Engine, starts_at: str) -> None:
    insert(db_engine, starts_at)


def test_duplicate_slot_for_same_specialty(db_engine: Engine) -> None:
    insert(db_engine, f"{MONDAY} 10:00:00", phone="5500000000001")
    rejects(
        db_engine,
        f"{MONDAY} 10:00:00",
        r"appointments\.specialty_id, appointments\.starts_at",
        phone="5500000000002",
    )


def test_same_slot_in_other_specialty_is_allowed(db_engine: Engine) -> None:
    insert(db_engine, f"{MONDAY} 10:00:00", specialty=1, phone="5500000000001")
    insert(db_engine, f"{MONDAY} 10:00:00", specialty=2, phone="5500000000002")


def test_patient_cannot_have_two_appointments_at_same_time(db_engine: Engine) -> None:
    insert(db_engine, f"{MONDAY} 10:00:00", specialty=1)
    rejects(
        db_engine,
        f"{MONDAY} 10:00:00",
        r"appointments\.patient_phone, appointments\.starts_at",
        specialty=2,
    )


def test_min_advance(db_engine: Engine) -> None:
    # Agora: 12/11 09:00, antecedência 120 min -> primeiro horário possível 11:00.
    rejects(db_engine, "2026-11-12 10:30:00", "too_soon")
    insert(db_engine, "2026-11-12 11:00:00")


def test_min_advance_uses_injected_clock(db_engine: Engine, clock: FixedClock) -> None:
    insert(db_engine, "2026-11-13 10:00:00", phone="5500000000001")
    clock.advance(timedelta(days=1))  # agora: 13/11 09:00
    rejects(db_engine, "2026-11-13 10:30:00", "too_soon", phone="5500000000002")


def test_past_appointment_is_rejected(db_engine: Engine) -> None:
    rejects(db_engine, "2026-11-10 10:00:00", "too_soon")


def test_cancelling_frees_the_slot(db_engine: Engine) -> None:
    appointment_id = insert(db_engine, f"{MONDAY} 10:00:00", phone="5500000000001")
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE appointments SET status = 'cancelled', "
                "cancelled_at = '2026-11-12 09:00:00' WHERE id = :id"
            ),
            {"id": appointment_id},
        )
    insert(db_engine, f"{MONDAY} 10:00:00", phone="5500000000002")


def test_cancelled_requires_cancelled_at(db_engine: Engine) -> None:
    appointment_id = insert(db_engine, f"{MONDAY} 10:00:00")
    with pytest.raises(IntegrityError, match="cancelled_at_consistent"), db_engine.begin() as conn:
        conn.execute(
            text("UPDATE appointments SET status = 'cancelled' WHERE id = :id"),
            {"id": appointment_id},
        )


def test_moving_appointment_outside_business_hours_is_rejected(db_engine: Engine) -> None:
    appointment_id = insert(db_engine, f"{MONDAY} 10:00:00")
    with (
        pytest.raises(IntegrityError, match="outside_business_hours"),
        db_engine.begin() as conn,
    ):
        conn.execute(
            text("UPDATE appointments SET starts_at = '2026-11-15 10:00:00' WHERE id = :id"),
            {"id": appointment_id},
        )


def test_moving_appointment_too_soon_is_rejected(db_engine: Engine) -> None:
    appointment_id = insert(db_engine, f"{MONDAY} 10:00:00")
    with pytest.raises(IntegrityError, match="too_soon"), db_engine.begin() as conn:
        conn.execute(
            text("UPDATE appointments SET starts_at = '2026-11-12 09:30:00' WHERE id = :id"),
            {"id": appointment_id},
        )


def test_foreign_keys_are_enforced(db_engine: Engine) -> None:
    rejects(db_engine, f"{MONDAY} 10:00:00", "FOREIGN KEY", specialty=999)


@pytest.mark.parametrize(
    ("statement", "error"),
    [
        ("UPDATE appointments SET status = 'pending'", "status_valid"),
        ("UPDATE appointments SET patient_name = '   '", "patient_name_not_empty"),
        ("UPDATE specialties SET price_cents = -1", "price_non_negative"),
        ("INSERT INTO schedule_settings VALUES (2, 10)", "single_row"),
        (
            "INSERT INTO business_hours (weekday, open_min, close_min) VALUES (7, 480, 600)",
            "weekday_range",
        ),
        (
            "INSERT INTO business_hours (weekday, open_min, close_min) VALUES (0, 600, 480)",
            "interval",
        ),
    ],
)
def test_domain_checks(db_engine: Engine, statement: str, error: str) -> None:
    insert(db_engine, f"{MONDAY} 10:00:00")
    with pytest.raises(IntegrityError, match=error), db_engine.begin() as conn:
        conn.execute(text(statement))


def test_connection_pragmas(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
        assert conn.exec_driver_sql("SELECT clinic_now()").scalar() == "2026-11-12 09:00:00"
