"""Bloqueios de agenda (feriados, férias) com trigger que recusa consultas nesses dias.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BLOCKED = """
    NEW.status = 'confirmed' AND EXISTS (
        SELECT 1 FROM schedule_blocks AS b
        WHERE date(NEW.starts_at) BETWEEN b.start_date AND b.end_date
          AND (b.specialty_id IS NULL OR b.specialty_id = NEW.specialty_id)
    )
"""

TRIGGERS = {
    "trg_appointments_blocked_insert": "BEFORE INSERT ON appointments",
    "trg_appointments_blocked_update": "BEFORE UPDATE OF starts_at, status ON appointments",
}


def upgrade() -> None:
    op.create_table(
        "schedule_blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("specialty_id", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(120), nullable=False),
        sa.CheckConstraint("start_date <= end_date", name="period"),
        sa.CheckConstraint("length(trim(reason)) > 0", name="reason_not_empty"),
        sa.ForeignKeyConstraint(
            ["specialty_id"],
            ["specialties.id"],
            name="fk_schedule_blocks_specialty_id_specialties",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_schedule_blocks"),
    )
    for name, timing in TRIGGERS.items():
        op.execute(
            f"CREATE TRIGGER {name} {timing} WHEN {_BLOCKED} "
            "BEGIN SELECT RAISE(ABORT, 'blocked_day'); END"
        )


def downgrade() -> None:
    for name in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("schedule_blocks")
