"""Registro de chamadas à IA (limites de uso e métricas).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
        sa.CheckConstraint("outcome IN ('answered', 'handoff', 'error')", name="outcome_valid"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_llm_calls_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_llm_calls"),
    )
    op.create_index(
        "ix_llm_calls_conversation_id_created_at", "llm_calls", ["conversation_id", "created_at"]
    )
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])


def downgrade() -> None:
    op.drop_table("llm_calls")
