"""Drop the announcements table: the 공지 page and new-announcement alerts were removed."""
from alembic import op
import sqlalchemy as sa

from app.db.base import UtcDateTime

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("ix_announcements_user_id", table_name="announcements")
    op.drop_index("ix_announcements_posted_at", table_name="announcements")
    op.drop_table("announcements")


def downgrade():
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("lms_course_id", sa.Integer(), nullable=False),
        sa.Column("course_name", sa.String(length=255), nullable=False),
        sa.Column("lms_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("posted_at", UtcDateTime(timezone=True), nullable=True),
        sa.Column("attachments", sa.JSON(), nullable=True),
        sa.Column("created_at", UtcDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "lms_id"),
    )
    op.create_index("ix_announcements_posted_at", "announcements", ["posted_at"], unique=False)
    op.create_index("ix_announcements_user_id", "announcements", ["user_id"], unique=False)
