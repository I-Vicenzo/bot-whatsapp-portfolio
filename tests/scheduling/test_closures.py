"""Bloqueios de agenda. Na clínica de teste: feriado em 19/11 (clínica toda) e congresso do
cardiologista em 17 e 18/11."""

from datetime import date, datetime
from pathlib import Path

import pytest
import yaml
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.ai.prompt import build_system_prompt
from app.clinic import ClinicData, ClinicDataError, load_clinic
from app.db.models import Channel, ScheduleBlock, Specialty
from app.scheduling import BlockedDay, Scheduler
from tests.conftest import CLINIC_FILE

HOLIDAY = date(2026, 11, 19)
CONGRESS = date(2026, 11, 17)


def insert(engine: Engine, starts_at: str, specialty: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO appointments (specialty_id, patient_phone, patient_name, starts_at,"
                " channel, created_at) VALUES (:s, '55', 'Ana Lima', :t, 'simulator',"
                " '2026-11-12 09:00:00')"
            ),
            {"s": specialty, "t": starts_at},
        )


def ids(session_factory: sessionmaker[Session]) -> dict[str, int]:
    with session_factory() as session:
        return {s.slug: s.id for s in session.scalars(select(Specialty))}


def test_blocks_are_synced(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        assert session.scalar(select(func.count(ScheduleBlock.id))) == 2


def test_database_rejects_holiday_for_everyone(
    db_engine: Engine, session_factory: sessionmaker[Session]
) -> None:
    for specialty_id in ids(session_factory).values():
        with pytest.raises(IntegrityError, match="blocked_day"):
            insert(db_engine, "2026-11-19 10:00:00", specialty_id)


def test_database_rejects_vacation_only_for_that_specialty(
    db_engine: Engine, session_factory: sessionmaker[Session]
) -> None:
    slugs = ids(session_factory)
    with pytest.raises(IntegrityError, match="blocked_day"):
        insert(db_engine, "2026-11-17 10:00:00", slugs["cardiologia"])
    insert(db_engine, "2026-11-17 10:00:00", slugs["clinica-geral"])


def test_scheduler_skips_blocked_days(
    scheduler: Scheduler, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        cardio = scheduler.get_specialty(session, "cardiologia")
        general = scheduler.get_specialty(session, "clinica-geral")
        cardio_days = scheduler.available_days(session, cardio)
        general_days = scheduler.available_days(session, general)
        assert scheduler.available_slots(session, cardio, CONGRESS) == []
        assert scheduler.available_slots(session, general, CONGRESS)
    assert HOLIDAY not in cardio_days
    assert HOLIDAY not in general_days
    assert CONGRESS not in cardio_days
    assert CONGRESS in general_days


def test_book_on_blocked_day_raises_domain_error(
    scheduler: Scheduler, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session, pytest.raises(BlockedDay):
        scheduler.book(
            session,
            specialty=scheduler.get_specialty(session, "clinica-geral"),
            starts_at=datetime(2026, 11, 19, 10, 0),
            patient_phone="55",
            patient_name="Ana Lima",
            channel=Channel.SIMULATOR,
        )


def test_prompt_lists_closures(clinic: ClinicData) -> None:
    prompt = build_system_prompt(clinic)
    assert "19/11/2026: feriado municipal (clínica fechada)" in prompt
    assert "17/11/2026 a 18/11/2026: congresso do Dr. Bruno (só Cardiologia)" in prompt


def write_with_closures(tmp_path: Path, closures: list[dict]) -> Path:
    data = yaml.safe_load(CLINIC_FILE.read_text(encoding="utf-8"))
    data["closures"] = closures
    path = tmp_path / "clinica.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_closure_formats(tmp_path: Path) -> None:
    clinic = load_clinic(
        write_with_closures(
            tmp_path,
            [
                {"date": "2026-12-25", "reason": "Natal"},
                {"start": "2027-01-04", "reason": "só início"},
                {"start": "2027-02-01", "end": "2027-02-03", "reason": "período"},
            ],
        )
    )
    first, second, third = clinic.closures
    assert first.start == first.end == date(2026, 12, 25)
    assert second.start == second.end == date(2027, 1, 4)
    assert (third.end - third.start).days == 2


@pytest.mark.parametrize(
    ("closure", "message"),
    [
        ({"start": "2027-01-10", "end": "2027-01-01", "reason": "x"}, "fim antes do início"),
        ({"date": "2027-01-10", "specialty": "ortopedia", "reason": "x"}, "desconhecida"),
        ({"date": "2027-01-10"}, "reason"),
    ],
)
def test_invalid_closures(tmp_path: Path, closure: dict, message: str) -> None:
    with pytest.raises(ClinicDataError, match=message):
        load_clinic(write_with_closures(tmp_path, [closure]))
