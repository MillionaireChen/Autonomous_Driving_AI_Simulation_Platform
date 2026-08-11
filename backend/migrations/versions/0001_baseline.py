"""Baseline: the schema as it stood when migrations were introduced.

Deliberately empty. Databases created before this point are stamped with this
revision; fresh databases are built from the models and stamped at head. Real
changes start at 0002.

Revision ID: 0001
Revises:
"""
from __future__ import annotations

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
