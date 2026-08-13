"""Retire a model without deleting its results.

Removing a model from configs/models.yaml used to leave the row behind: the
registry sync only ever upserts, so the dashboard kept offering endpoints
nobody served, and picking one failed at connect time.

Deleting the row is not the fix. Experiments carry a foreign key to their
model, so `DELETE FROM models` fails against every run that model ever did -
and forcing it through would mean destroying the evaluation history, which is
the one thing here that cannot be regenerated.

So a model is retired instead: the row stays, the results stay, and the API
stops listing it.

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column("archived", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("models", "archived")
