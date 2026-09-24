"""add media issue reporting

Revision ID: 041
Revises: 040
"""
from alembic import op
import sqlalchemy as sa


revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "media_issues",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=True),
        sa.Column("format", sa.String(length=16), nullable=True),
        sa.Column("download_task_id", sa.Integer(), nullable=True),
        sa.Column("media_key", sa.String(length=255), nullable=False),
        sa.Column("issue_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("active_key", sa.String(length=64), nullable=True),
        sa.Column("report_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_critical", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("critical_report_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("critical_first_reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_reported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_reported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_by_user_id", sa.Integer(), nullable=True),
        sa.Column("done_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["download_task_id"], ["download_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["done_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_issues_public_id", "media_issues", ["public_id"], unique=True)
    op.create_index("ix_media_issues_book_id", "media_issues", ["book_id"], unique=False)
    op.create_index("ix_media_issues_format", "media_issues", ["format"], unique=False)
    op.create_index("ix_media_issues_download_task_id", "media_issues", ["download_task_id"], unique=False)
    op.create_index("ix_media_issues_issue_type", "media_issues", ["issue_type"], unique=False)
    op.create_index("ix_media_issues_status", "media_issues", ["status"], unique=False)
    op.create_index("ix_media_issues_fingerprint", "media_issues", ["fingerprint"], unique=False)
    op.create_index("ix_media_issues_active_key", "media_issues", ["active_key"], unique=True)
    op.create_index("ix_media_issues_is_critical", "media_issues", ["is_critical"], unique=False)

    op.create_table(
        "media_issue_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("issue_id", sa.Integer(), nullable=False),
        sa.Column("reporter_user_id", sa.Integer(), nullable=True),
        sa.Column("reporter_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("report_text", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=True),
        sa.Column("download_task_id", sa.Integer(), nullable=True),
        sa.Column("context_json", sa.Text(), nullable=True),
        sa.Column("is_critical", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("critical_explanation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["issue_id"], ["media_issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reporter_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["request_id"], ["book_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["download_task_id"], ["download_tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issue_id", "reporter_user_id", name="uq_media_issue_reporter"),
    )
    op.create_index("ix_media_issue_reports_issue_id", "media_issue_reports", ["issue_id"], unique=False)
    op.create_index("ix_media_issue_reports_reporter_user_id", "media_issue_reports", ["reporter_user_id"], unique=False)

    op.create_table(
        "media_issue_status_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("issue_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("previous_status", sa.String(length=16), nullable=True),
        sa.Column("new_status", sa.String(length=16), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["issue_id"], ["media_issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_issue_status_events_issue_id", "media_issue_status_events", ["issue_id"], unique=False)

    op.create_table(
        "issue_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), nullable=False),
        sa.Column("issue_id", sa.Integer(), nullable=False),
        sa.Column("status_event_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issue_id"], ["media_issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["status_event_id"], ["media_issue_status_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recipient_user_id",
            "status_event_id",
            "kind",
            name="uq_issue_notification_event_recipient",
        ),
    )
    op.create_index("ix_issue_notifications_recipient_user_id", "issue_notifications", ["recipient_user_id"], unique=False)
    op.create_index("ix_issue_notifications_issue_id", "issue_notifications", ["issue_id"], unique=False)
    op.create_index("ix_issue_notifications_read_at", "issue_notifications", ["read_at"], unique=False)


def downgrade():
    op.drop_table("issue_notifications")
    op.drop_table("media_issue_status_events")
    op.drop_table("media_issue_reports")
    op.drop_table("media_issues")
