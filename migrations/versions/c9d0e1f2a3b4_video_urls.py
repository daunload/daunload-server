"""Record the media URL the playback worker actually played, one row per content_id."""
from alembic import op
import sqlalchemy as sa

from app.db.base import UtcDateTime

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "video_urls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("content_id", sa.String(length=64), nullable=False),
        sa.Column("media_url", sa.Text(), nullable=False),
        sa.Column("lms_course_id", sa.Integer(), nullable=False),
        sa.Column("module_item_id", sa.Integer(), nullable=False),
        sa.Column("course_name", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("first_user_id", sa.Integer(), nullable=True),
        sa.Column("last_user_id", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="1", nullable=False),
        sa.Column("first_seen_at", UtcDateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", UtcDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["first_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["last_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_id"),
    )
    op.create_index("ix_video_urls_last_seen_at", "video_urls", ["last_seen_at"], unique=False)


def downgrade():
    op.drop_index("ix_video_urls_last_seen_at", table_name="video_urls")
    op.drop_table("video_urls")
