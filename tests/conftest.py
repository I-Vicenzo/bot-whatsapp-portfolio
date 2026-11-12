"""Fixtures compartilhadas.

Banco: SQLite por padrão. Com TEST_DATABASE_URL (postgresql+psycopg://...), os testes que usam
`db_engine` rodam no PostgreSQL, cada um em um schema próprio; os marcados `sqlite_only` são
pulados.

Data de referência: quinta-feira, 12/11/2026, 09:00 (America/Sao_Paulo). Clínica de teste em
tests/fixtures/clinic.yaml: seg-sex 08:00-12:00 e 13:30-18:00, sáb 08:00-12:00, domingo fechado,
antecedência mínima de 120 min, agenda aberta até hoje + 14 dias.
"""

import os
import shutil
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, create_engine
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
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
POSTGRES = TEST_DATABASE_URL.startswith("postgresql")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not POSTGRES:
        return
    skip = pytest.mark.skip(reason="específico do SQLite")
    for item in items:
        if "sqlite_only" in item.keywords:
            item.add_marker(skip)


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
def db_engine(
    tmp_path: Path, migrated_template: Path, clock: FixedClock, clinic: ClinicData
) -> Iterator[Engine]:
    if POSTGRES:
        yield from _postgres_engine(clock, clinic)
        return
    path = tmp_path / "app.db"
    shutil.copyfile(migrated_template, path)
    engine = make_engine(f"sqlite:///{path}", clock)
    yield engine
    engine.dispose()


def _postgres_engine(clock: FixedClock, clinic: ClinicData) -> Iterator[Engine]:
    schema = f"t_{uuid4().hex[:12]}"
    admin = create_engine(TEST_DATABASE_URL)
    with admin.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = make_engine(
        TEST_DATABASE_URL, clock, connect_args={"options": f"-csearch_path={schema}"}
    )
    try:
        run_migrations(engine)
        with make_session_factory(engine).begin() as session:
            sync_clinic(session, clinic)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


@pytest.fixture
def session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(db_engine)


@pytest.fixture
def scheduler(clinic: ClinicData, clock: FixedClock) -> Scheduler:
    return Scheduler(clinic, clock)
