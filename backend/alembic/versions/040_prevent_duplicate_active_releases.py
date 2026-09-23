"""prevent duplicate active releases

Revision ID: 040
Revises: 039
"""
from alembic import op
import sqlalchemy as sa


revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade():
    active_predicate = sa.text(
        "info_hash IS NOT NULL AND state IN ('queued', 'downloading', 'checking', 'processing', 'paused')"
    )
    op.create_index(
        "uq_download_tasks_active_release",
        "download_tasks",
        ["book_id", "format", "info_hash"],
        unique=True,
        postgresql_where=active_predicate,
        sqlite_where=active_predicate,
    )


def downgrade():
    op.drop_index("uq_download_tasks_active_release", table_name="download_tasks")
