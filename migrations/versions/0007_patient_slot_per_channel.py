"""Índice "um paciente, um horário" passa a valer por canal.

O simulador é público e aceita qualquer número. Com o índice só em (telefone, horário), quem
digitasse o número de um paciente do WhatsApp descobriria, pela recusa, que ele tem consulta
naquele horário. Cada canal agora é um espaço de contatos separado.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "uq_appointments_patient_slot"
CONFIRMED = sa.text("status = 'confirmed'")


def _recreate(columns: list[str]) -> None:
    op.drop_index(INDEX, table_name="appointments")
    op.create_index(
        INDEX,
        "appointments",
        columns,
        unique=True,
        sqlite_where=CONFIRMED,
        postgresql_where=CONFIRMED,
    )


def upgrade() -> None:
    _recreate(["channel", "patient_phone", "starts_at"])


def downgrade() -> None:
    _recreate(["patient_phone", "starts_at"])
