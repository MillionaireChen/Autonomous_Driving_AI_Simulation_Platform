"""Model display order.

The dashboard selects the first model in the list by default, and sorting by id
put cnn_il first - the one model that fails the scenario. Order belongs to the
registry (configs/models.yaml) rather than to the UI.

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column("display_order", sa.Integer(), nullable=False,
                  server_default="100"),
    )


def downgrade() -> None:
    op.drop_column("models", "display_order")
