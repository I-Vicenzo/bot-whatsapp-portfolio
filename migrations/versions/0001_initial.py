"""Esquema inicial: tabelas, constraints, índices únicos parciais e triggers de agenda.

Regras de agenda garantidas aqui (o código valida antes; o banco é a última barreira):
- intervalos de 30 min e formato canônico de `starts_at` (CHECK);
- um paciente/horário e uma especialidade/horário por consulta confirmada (UNIQUE parcial);
- horário de funcionamento (trigger, consulta `business_hours`);
- antecedência mínima (trigger, usa `clinic_now()` registrada pela aplicação).

Nomes de CHECK são curtos: a convenção de `Base.metadata` acrescenta `ck_<tabela>_`.

Revision ID: 0001
Revises:
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCAL_DATETIME = sa.DateTime()

# Minutos desde 00:00 e dia da semana (0 = segunda, como em Python) de NEW.starts_at.
_START_MIN = (
    "(CAST(strftime('%H', NEW.starts_at) AS INTEGER) * 60"
    " + CAST(strftime('%M', NEW.starts_at) AS INTEGER))"
)
_WEEKDAY = "((CAST(strftime('%w', NEW.starts_at) AS INTEGER) + 6) % 7)"

# Só constantes interpoladas; nenhum dado externo.
_OUTSIDE_HOURS = f"""
    NEW.status = 'confirmed' AND NOT EXISTS (
        SELECT 1 FROM business_hours AS bh
        WHERE bh.weekday = {_WEEKDAY}
          AND {_START_MIN} >= bh.open_min
          AND {_START_MIN} + 30 <= bh.close_min
    )
"""

_TOO_SOON = """
    NEW.status = 'confirmed' AND NEW.starts_at < datetime(
        clinic_now(),
        '+' || (SELECT coalesce(max(min_advance_minutes), 0) FROM schedule_settings) || ' minutes'
    )
"""

TRIGGERS = {
    "trg_appointments_business_hours_insert": (
        "BEFORE INSERT ON appointments",
        _OUTSIDE_HOURS,
        "outside_business_hours",
    ),
    "trg_appointments_business_hours_update": (
        "BEFORE UPDATE OF starts_at, status ON appointments",
        _OUTSIDE_HOURS,
        "outside_business_hours",
    ),
    "trg_appointments_min_advance_insert": (
        "BEFORE INSERT ON appointments",
        _TOO_SOON,
        "too_soon",
    ),
    "trg_appointments_min_advance_update": (
        "BEFORE UPDATE OF starts_at ON appointments",
        _TOO_SOON,
        "too_soon",
    ),
}


def upgrade() -> None:
    op.create_table(
        "specialties",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(60), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("professional", sa.String(100), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.CheckConstraint("price_cents >= 0", name="price_non_negative"),
        sa.CheckConstraint("length(trim(name)) > 0", name="name_not_empty"),
        sa.CheckConstraint("length(trim(slug)) > 0", name="slug_not_empty"),
        sa.UniqueConstraint("slug", name="uq_specialties_slug"),
        sa.PrimaryKeyConstraint("id", name="pk_specialties"),
    )

    op.create_table(
        "business_hours",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("weekday", sa.Integer(), nullable=False, comment="0 = segunda ... 6 = domingo"),
        sa.Column("open_min", sa.Integer(), nullable=False),
        sa.Column("close_min", sa.Integer(), nullable=False),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        sa.CheckConstraint(
            "open_min >= 0 AND close_min <= 1440 AND open_min < close_min",
            name="interval",
        ),
        sa.CheckConstraint("open_min % 30 = 0 AND close_min % 30 = 0", name="slot_aligned"),
        sa.PrimaryKeyConstraint("id", name="pk_business_hours"),
    )

    op.create_table(
        "schedule_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("min_advance_minutes", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="single_row"),
        sa.CheckConstraint("min_advance_minutes >= 0", name="min_advance_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_schedule_settings"),
    )

    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("specialty_id", sa.Integer(), nullable=False),
        sa.Column("patient_phone", sa.String(32), nullable=False),
        sa.Column("patient_name", sa.String(120), nullable=False),
        sa.Column("starts_at", LOCAL_DATETIME, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="confirmed"),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("created_at", LOCAL_DATETIME, nullable=False),
        sa.Column("cancelled_at", LOCAL_DATETIME, nullable=True),
        sa.CheckConstraint(
            "starts_at = coalesce(strftime('%Y-%m-%d %H:%M:%S', starts_at), '')",
            name="starts_at_format",
        ),
        sa.CheckConstraint(
            "coalesce(strftime('%M:%S', starts_at), '') IN ('00:00', '30:00')",
            name="starts_at_slot",
        ),
        sa.CheckConstraint("status IN ('confirmed', 'cancelled')", name="status_valid"),
        sa.CheckConstraint("channel IN ('whatsapp', 'simulator', 'admin')", name="channel_valid"),
        sa.CheckConstraint("length(trim(patient_name)) > 0", name="patient_name_not_empty"),
        sa.CheckConstraint("length(trim(patient_phone)) > 0", name="patient_phone_not_empty"),
        sa.CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)",
            name="cancelled_at_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["specialty_id"],
            ["specialties.id"],
            name="fk_appointments_specialty_id_specialties",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_appointments"),
    )
    op.create_index(
        "uq_appointments_specialty_slot",
        "appointments",
        ["specialty_id", "starts_at"],
        unique=True,
        sqlite_where=sa.text("status = 'confirmed'"),
    )
    op.create_index(
        "uq_appointments_patient_slot",
        "appointments",
        ["patient_phone", "starts_at"],
        unique=True,
        sqlite_where=sa.text("status = 'confirmed'"),
    )
    op.create_index("ix_appointments_starts_at", "appointments", ["starts_at"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(60), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", LOCAL_DATETIME, nullable=False),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("contact", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="bot"),
        sa.Column("state", sa.String(32), nullable=False, server_default="MENU"),
        sa.Column("context", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("priority", sa.String(8), nullable=False, server_default="normal"),
        sa.Column("handoff_reason", sa.String(200), nullable=True),
        sa.Column("assigned_to", sa.Integer(), nullable=True),
        sa.Column("unknown_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_inbound_at", LOCAL_DATETIME, nullable=True),
        sa.Column("created_at", LOCAL_DATETIME, nullable=False),
        sa.CheckConstraint(
            "channel IN ('whatsapp', 'simulator', 'admin')",
            name="channel_valid",
        ),
        sa.CheckConstraint("status IN ('bot', 'waiting_human', 'human')", name="status_valid"),
        sa.CheckConstraint("priority IN ('normal', 'urgent')", name="priority_valid"),
        sa.CheckConstraint("unknown_count >= 0", name="unknown_count_non_negative"),
        sa.ForeignKeyConstraint(
            ["assigned_to"], ["users.id"], name="fk_conversations_assigned_to_users"
        ),
        sa.UniqueConstraint("channel", "contact", name="uq_conversations_channel_contact"),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("author", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=True),
        sa.Column("created_at", LOCAL_DATETIME, nullable=False),
        sa.CheckConstraint("direction IN ('in', 'out')", name="direction_valid"),
        sa.CheckConstraint("author IN ('customer', 'bot', 'ai', 'agent')", name="author_valid"),
        sa.CheckConstraint(
            "(direction = 'in') = (author = 'customer')",
            name="author_matches_direction",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_messages_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("external_id", name="uq_messages_external_id"),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
    )
    op.create_index("ix_messages_conversation_id_id", "messages", ["conversation_id", "id"])

    for name, (timing, condition, error) in TRIGGERS.items():
        op.execute(
            f"CREATE TRIGGER {name} {timing} WHEN {condition} "
            f"BEGIN SELECT RAISE(ABORT, '{error}'); END"
        )


def downgrade() -> None:
    for name in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("users")
    op.drop_table("appointments")
    op.drop_table("schedule_settings")
    op.drop_table("business_hours")
    op.drop_table("specialties")
