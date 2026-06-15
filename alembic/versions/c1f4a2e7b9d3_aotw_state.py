"""aotw state + pins

Revision ID: c1f4a2e7b9d3
Revises: b280bde90a9d
Create Date: 2026-06-15 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1f4a2e7b9d3"
down_revision: Union[str, None] = "b280bde90a9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "aotw_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("achievement_id", sa.Integer(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "aotw_pins",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("achievement_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("guild_id"),
    )


def downgrade() -> None:
    op.drop_table("aotw_pins")
    op.drop_table("aotw_state")
