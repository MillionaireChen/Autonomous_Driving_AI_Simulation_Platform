"""Frame recording: experiments.record_frames and the frames table.

Adds the opt-in recording flag and the per-frame metadata table that Phase 9
replay needs. Images themselves stay on disk (spec section 42); only their
location is stored.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column("record_frames", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )
    op.create_table(
        "frames",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("experiment_id", sa.String(32),
                  sa.ForeignKey("experiments.id"), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("tick", sa.Integer(), nullable=False),
        sa.Column("sim_time", sa.Float(), nullable=False),
        sa.Column("path", sa.String(256), nullable=False),
    )
    op.create_index("ix_frames_experiment_id", "frames", ["experiment_id"])


def downgrade() -> None:
    op.drop_index("ix_frames_experiment_id", table_name="frames")
    op.drop_table("frames")
    op.drop_column("experiments", "record_frames")
