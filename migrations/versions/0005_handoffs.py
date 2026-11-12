"""Histórico de encaminhamentos para humano (métricas de tempo até o atendimento).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "handoffs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("first_response_at", sa.DateTime(), nullable=True),
        sa.Column("priority", sa.String(8), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.CheckConstraint("priority IN ('normal', 'urgent')", name="priority_valid"),
        sa.CheckConstraint(
            "first_response_at IS NULL OR first_response_at >= requested_at",
            name="response_after_request",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_handoffs_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_handoffs"),
    )
    op.create_index("ix_handoffs_requested_at", "handoffs", ["requested_at"])
    op.create_index("ix_handoffs_conversation_id", "handoffs", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("handoffs")
