"""Fixtures compartilhadas.

Data de referência: quinta-feira, 12/11/2026, 09:00 (America/Sao_Paulo). Clínica de teste em
tests/fixtures/clinic.yaml: seg-sex 08:00-12:00 e 13:30-18:00, sáb 08:00-12:00, domingo fechado,
antecedência mínima de 120 min, agenda aberta até hoje + 14 dias.
"""

import shutil
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.clinic import ClinicData, load_clinic
from app.clock import FixedClock
from app.db.engine import make_engine, make_session_factory
from app.db.migrate import run_migrations
from app.db.sync import sync_clinic
from app.scheduling import Scheduler

TZ = ZoneInfo("America/Sao_Paulo")
NOW = datetime(2026, 11, 12, 9, 0, tzinfo=TZ)  # quinta-feira
FIXTURES = Path(__file__).parent / "fixtures"
CLINIC_FILE = FIXTURES / "clinic.yaml"


@pytest.fixture(scope="session")
def clinic() -> ClinicData:
    return load_clinic(CLINIC_FILE)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory, clinic: ClinicData) -> Path:
    """Banco migrado e sincronizado uma vez; cada teste recebe uma cópia."""
    path = tmp_path_factory.mktemp("template") / "template.db"
    engine = make_engine(f"sqlite:///{path}", FixedClock(NOW))
    run_migrations(engine)
    with make_session_factory(engine).begin() as session:
        sync_clinic(session, clinic)
    engine.dispose()
    return path


@pytest.fixture
def db_engine(tmp_path: Path, migrated_template: Path, clock: FixedClock) -> Iterator[Engine]:
    path = tmp_path / "app.db"
    shutil.copyfile(migrated_template, path)
    engine = make_engine(f"sqlite:///{path}", clock)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(db_engine)


@pytest.fixture
def scheduler(clinic: ClinicData, clock: FixedClock) -> Scheduler:
    return Scheduler(clinic, clock)
