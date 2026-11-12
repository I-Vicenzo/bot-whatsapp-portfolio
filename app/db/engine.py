"""Engine e sessões, com os hooks de conexão do SQLite.

Toda conexão recebe:
- `foreign_keys=ON` (o SQLite não aplica FKs por padrão);
- `journal_mode=WAL` e `busy_timeout` (leitores não bloqueiam escritor; escritores esperam);
- a função SQL `clinic_now()`, alimentada pelo relógio injetável, usada pelos triggers.

Transações começam com `BEGIN IMMEDIATE`: escritas concorrentes são serializadas pelo
`busy_timeout` em vez de falharem com "database is locked" ao promover uma leitura a escrita.
"""

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.clock import DB_DATETIME_FORMAT, Clock

BUSY_TIMEOUT_MS = 5000


def make_engine(database_url: str, clock: Clock) -> Engine:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise ValueError(f"Banco não suportado nesta versão: {url.get_backend_name()}")

    database = url.database or ""
    in_memory = database in ("", ":memory:")
    if not in_memory:
        Path(database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        # Desliga o controle de transação do driver; o evento "begin" abaixo assume.
        dbapi_connection.isolation_level = None
        dbapi_connection.create_function(
            "clinic_now", 0, lambda: clock.local_now().strftime(DB_DATETIME_FORMAT)
        )
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        if not in_memory:
            cursor.execute("PRAGMA journal_mode = WAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(connection: Any) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
