"""users: cached award counts

Revision ID: e5c7a9d04b21
Revises: d8a3f1b5e2c4
Create Date: 2026-06-22 14:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5c7a9d04b21"
down_revision: Union[str, None] = "d8a3f1b5e2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = (
    "mastery_count",
    "completion_count",
    "beaten_hardcore_count",
    "beaten_softcore_count",
)


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        for name in _COLUMNS:
            batch_op.add_column(
                sa.Column(name, sa.Integer(), nullable=False, server_default="0")
            )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        for name in reversed(_COLUMNS):
            batch_op.drop_column(name)
