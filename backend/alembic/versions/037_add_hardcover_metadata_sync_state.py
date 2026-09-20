"""add Hardcover metadata reconciliation state

Revision ID: 037
Revises: 036
Create Date: 2026-09-19

"""
from alembic import op
import sqlalchemy as sa


revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("books", sa.Column("hardcover_metadata_status", sa.String(), nullable=True))
    op.add_column(
        "books",
        sa.Column("hardcover_metadata_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_books_hardcover_metadata_status",
        "books",
        ["hardcover_metadata_status"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_books_hardcover_metadata_status", table_name="books")
    op.drop_column("books", "hardcover_metadata_checked_at")
    op.drop_column("books", "hardcover_metadata_status")
