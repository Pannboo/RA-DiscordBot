"""guild_configs.game_lookup_public

Revision ID: d8a3f1b5e2c4
Revises: c1f4a2e7b9d3
Create Date: 2026-06-15 18:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8a3f1b5e2c4"
down_revision: Union[str, None] = "c1f4a2e7b9d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("guild_configs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "game_lookup_public",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("guild_configs") as batch_op:
        batch_op.drop_column("game_lookup_public")
