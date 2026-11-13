"""Falha definitiva de lembrete e ids de mensagens apagadas (LGPD).

- `appointments.reminder_failed_at`: a Meta recusou o lembrete de forma definitiva (4xx, ex.:
  template não aprovado ou número inválido). Sem isso, o ciclo reenviava a cada 5 minutos.
- `erased_message_ids`: ids externos (da Meta) das mensagens apagadas a pedido do cliente. A
  deduplicação de webhooks depende do índice único em `messages.external_id`; ao apagar a
  conversa, um reenvio da Meta da última mensagem ("sim") recriaria a conversa. O id externo é
  opaco e não identifica o cliente.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("appointments") as batch:
        batch.add_column(sa.Column("reminder_failed_at", sa.DateTime(), nullable=True))
    op.create_table(
        "erased_message_ids",
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("external_id", name="pk_erased_message_ids"),
    )


def downgrade() -> None:
    op.drop_table("erased_message_ids")
    with op.batch_alter_table("appointments") as batch:
        batch.drop_column("reminder_failed_at")
