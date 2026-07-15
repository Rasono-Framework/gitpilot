"""create operations table"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260715_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("op_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column("repo", sa.String(length=255), nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("path", sa.String(length=1024), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("github_request_id", sa.String(length=128), nullable=True),
        sa.Column("github_status", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_operations_op_type"), "operations", ["op_type"], unique=False)
    op.create_index(op.f("ix_operations_status"), "operations", ["status"], unique=False)
    op.create_index(op.f("ix_operations_owner"), "operations", ["owner"], unique=False)
    op.create_index(op.f("ix_operations_repo"), "operations", ["repo"], unique=False)
    op.create_index(op.f("ix_operations_request_id"), "operations", ["request_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_operations_request_id"), table_name="operations")
    op.drop_index(op.f("ix_operations_repo"), table_name="operations")
    op.drop_index(op.f("ix_operations_owner"), table_name="operations")
    op.drop_index(op.f("ix_operations_status"), table_name="operations")
    op.drop_index(op.f("ix_operations_op_type"), table_name="operations")
    op.drop_table("operations")
