"""Migração x modelos, sincronização do YAML e idempotência da inicialização."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.clinic import ClinicData
from app.db.migrate import run_migrations
from app.db.models import Base, BusinessHour, ScheduleSettings, Specialty
from app.db.sync import sync_clinic


def test_models_match_migration(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_migrations_are_idempotent(db_engine: Engine) -> None:
    run_migrations(db_engine)
    assert "appointments" in inspect(db_engine).get_table_names()


def test_triggers_exist(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        names = set(
            conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'trigger'").scalars()
        )
    assert names == {
        "trg_appointments_business_hours_insert",
        "trg_appointments_business_hours_update",
        "trg_appointments_min_advance_insert",
        "trg_appointments_min_advance_update",
    }


def test_sync_loads_yaml(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        specialties = {s.slug: s for s in session.scalars(select(Specialty))}
        assert specialties["cardiologia"].price_cents == 28050
        assert specialties["cardiologia"].professional == "Dr. Bruno Teste"
        hours = session.scalars(select(BusinessHour)).all()
        assert len(hours) == 11  # 5 dias x 2 intervalos + sábado
        assert session.get(ScheduleSettings, 1).min_advance_minutes == 120  # type: ignore[union-attr]


def test_sync_deactivates_removed_specialty_and_is_idempotent(
    session_factory: sessionmaker[Session], clinic: ClinicData
) -> None:
    only_first = clinic.model_copy(update={"specialties": clinic.specialties[:1]})
    for _ in range(2):
        with session_factory.begin() as session:
            sync_clinic(session, only_first)

    with session_factory() as session:
        active = {s.slug: s.active for s in session.scalars(select(Specialty))}
        assert active == {"clinica-geral": True, "cardiologia": False}
        assert len(session.scalars(select(BusinessHour)).all()) == 11
