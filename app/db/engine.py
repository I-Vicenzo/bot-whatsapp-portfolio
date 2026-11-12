"""Engine e sessões, com os hooks de conexão de cada banco.

SQLite (padrão) — toda conexão recebe:
- `foreign_keys=ON` (o SQLite não aplica FKs por padrão);
- `journal_mode=WAL` e `busy_timeout` (leitores não bloqueiam escritor; escritores esperam);
- a função SQL `clinic_now()`, alimentada pelo relógio injetável, usada pelos triggers.
Transações começam com `BEGIN IMMEDIATE`: escritas concorrentes são serializadas pelo
`busy_timeout` em vez de falharem com "database is locked" ao promover uma leitura a escrita.

PostgreSQL — `clinic_now()` é criada pela migração e lê `app.now`, gravado no início de cada
transação a partir do mesmo relógio. Banco e código continuam usando um só "agora".
"""

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.clock import DB_DATETIME_FORMAT, Clock

BUSY_TIMEOUT_MS = 5000
SUPPORTED_BACKENDS = ("sqlite", "postgresql")


def make_engine(database_url: str, clock: Clock, **engine_kwargs: Any) -> Engine:
    url = make_url(database_url)
    backend = url.get_backend_name()
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"Banco não suportado: {backend} (use sqlite ou postgresql)")
    if backend == "postgresql":
        return _postgres_engine(url, clock, **engine_kwargs)
    return _sqlite_engine(url, clock, **engine_kwargs)


def _sqlite_engine(url: Any, clock: Clock, **engine_kwargs: Any) -> Engine:
    database = url.database or ""
    in_memory = database in ("", ":memory:")
    if not in_memory:
        Path(database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, **engine_kwargs)

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


def _postgres_engine(url: Any, clock: Clock, **engine_kwargs: Any) -> Engine:
    engine = create_engine(url, pool_pre_ping=True, **engine_kwargs)

    @event.listens_for(engine, "begin")
    def _on_begin(connection: Any) -> None:
        # `true` = vale só para esta transação.
        connection.execute(
            text("SELECT set_config('app.now', :now, true)"),
            {"now": clock.local_now().strftime(DB_DATETIME_FORMAT)},
        )

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
