"""add automatic approved-request fulfillment

Revision ID: 038
Revises: 037
Create Date: 2026-09-20

"""
from alembic import op
import sqlalchemy as sa


revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("book_requests", sa.Column("auto_search_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("book_requests", sa.Column("last_search_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("book_requests", sa.Column("next_search_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("book_requests", sa.Column("last_search_error", sa.Text(), nullable=True))
    op.add_column("book_requests", sa.Column("search_claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("book_requests", sa.Column("download_task_id", sa.Integer(), nullable=True))
    op.create_index("ix_book_requests_next_search_at", "book_requests", ["next_search_at"], unique=False)
    op.create_index("ix_book_requests_search_claimed_at", "book_requests", ["search_claimed_at"], unique=False)
    op.create_index("ix_book_requests_download_task_id", "book_requests", ["download_task_id"], unique=False)

    op.add_column("download_tasks", sa.Column("request_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_download_tasks_request_id",
        "download_tasks",
        "book_requests",
        ["request_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_download_tasks_request_id", "download_tasks", ["request_id"], unique=False)
    op.create_index(
        "uq_download_tasks_active_request",
        "download_tasks",
        ["request_id"],
        unique=True,
        postgresql_where=sa.text("request_id IS NOT NULL AND state IN ('queued', 'downloading', 'checking', 'processing', 'paused')"),
    )

    op.create_table(
        "auto_download_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("process_existing_backlog", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="900"),
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("max_active_downloads", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("minimum_score", sa.Float(), nullable=False, server_default="70"),
        sa.Column("ebook_formats_json", sa.Text(), nullable=False, server_default='["epub", "azw3", "mobi", "pdf"]'),
        sa.Column("audiobook_formats_json", sa.Text(), nullable=False, server_default='["m4b", "mp3", "flac"]'),
        sa.Column("preferred_languages_json", sa.Text(), nullable=False, server_default='["en"]'),
        sa.Column("protocol_order_json", sa.Text(), nullable=False, server_default='["usenet", "torrent"]'),
        sa.Column("minimum_seeders", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("ebook_min_size_mb", sa.Float(), nullable=False, server_default="0.1"),
        sa.Column("ebook_max_size_mb", sa.Float(), nullable=False, server_default="500"),
        sa.Column("audiobook_min_size_mb", sa.Float(), nullable=False, server_default="10"),
        sa.Column("audiobook_max_size_mb", sa.Float(), nullable=False, server_default="5000"),
        sa.Column("retry_schedule_json", sa.Text(), nullable=False, server_default="[900, 3600, 21600, 86400]"),
        sa.Column("categoryless_fallback", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_summary_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "fulfillment_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("download_task_id", sa.Integer(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("selected_release_title", sa.Text(), nullable=True),
        sa.Column("selected_score", sa.Float(), nullable=True),
        sa.Column("score_details_json", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["download_task_id"], ["download_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["request_id"], ["book_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fulfillment_attempts_request_id", "fulfillment_attempts", ["request_id"], unique=False)
    op.create_index("ix_fulfillment_attempts_outcome", "fulfillment_attempts", ["outcome"], unique=False)

    settings = sa.table(
        "auto_download_settings",
        sa.column("id", sa.Integer),
        sa.column("enabled", sa.Boolean),
        sa.column("dry_run", sa.Boolean),
        sa.column("process_existing_backlog", sa.Boolean),
    )
    op.bulk_insert(settings, [{"id": 1, "enabled": False, "dry_run": True, "process_existing_backlog": True}])


def downgrade():
    op.drop_index("ix_fulfillment_attempts_outcome", table_name="fulfillment_attempts")
    op.drop_index("ix_fulfillment_attempts_request_id", table_name="fulfillment_attempts")
    op.drop_table("fulfillment_attempts")
    op.drop_table("auto_download_settings")
    op.drop_index("ix_download_tasks_request_id", table_name="download_tasks")
    op.drop_index("uq_download_tasks_active_request", table_name="download_tasks")
    op.drop_constraint("fk_download_tasks_request_id", "download_tasks", type_="foreignkey")
    op.drop_column("download_tasks", "request_id")
    op.drop_index("ix_book_requests_download_task_id", table_name="book_requests")
    op.drop_index("ix_book_requests_search_claimed_at", table_name="book_requests")
    op.drop_index("ix_book_requests_next_search_at", table_name="book_requests")
    op.drop_column("book_requests", "download_task_id")
    op.drop_column("book_requests", "search_claimed_at")
    op.drop_column("book_requests", "last_search_error")
    op.drop_column("book_requests", "next_search_at")
    op.drop_column("book_requests", "last_search_at")
    op.drop_column("book_requests", "auto_search_attempts")
