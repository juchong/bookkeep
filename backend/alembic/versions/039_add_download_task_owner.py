"""add download task owner

Revision ID: 039
Revises: 038
"""
from alembic import op
import sqlalchemy as sa


revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("download_tasks", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_download_tasks_user_id",
        "download_tasks",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_download_tasks_user_id", "download_tasks", ["user_id"], unique=False)
    op.execute(
        """
        UPDATE download_tasks
        SET user_id = book_requests.user_id
        FROM book_requests
        WHERE download_tasks.request_id = book_requests.id
          AND download_tasks.user_id IS NULL
        """
    )


def downgrade():
    op.drop_index("ix_download_tasks_user_id", table_name="download_tasks")
    op.drop_constraint("fk_download_tasks_user_id", "download_tasks", type_="foreignkey")
    op.drop_column("download_tasks", "user_id")
