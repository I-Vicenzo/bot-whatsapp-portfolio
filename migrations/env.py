"""Ambiente do Alembic.

Na aplicação, a conexão chega pronta em `config.attributes["connection"]` (com os hooks de
`app.db.engine`). Pela linha de comando (`uv run alembic ...`), usa DATABASE_URL das Settings.
"""

from zoneinfo import ZoneInfo

from alembic import context
from sqlalchemy import Connection

from app.clock import SystemClock
from app.config import get_settings
from app.db.engine import make_engine
from app.db.models import Base

target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # ALTER TABLE no SQLite via recriação da tabela
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return

    # DDL não usa clinic_now(); o fuso aqui é irrelevante.
    engine = make_engine(get_settings().database_url, SystemClock(ZoneInfo("America/Sao_Paulo")))
    try:
        with engine.begin() as conn:
            _run(conn)
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Modo offline não suportado.")
run_migrations_online()
