"""session replay re-announcement

Revision ID: f7d9e02c8a14
Revises: e5c7a9d04b21
Create Date: 2026-06-22 16:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7d9e02c8a14"
down_revision: Union[str, None] = "e5c7a9d04b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("game_sessions") as batch_op:
        batch_op.add_column(sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("guild_configs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "replay_announce_after_minutes",
                sa.Integer(),
                nullable=False,
                server_default="360",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("guild_configs") as batch_op:
        batch_op.drop_column("replay_announce_after_minutes")
    with op.batch_alter_table("game_sessions") as batch_op:
        batch_op.drop_column("last_seen_at")
