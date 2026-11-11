"""Store each saved lecture video once (saved_video_items) and keep only user links in saved_videos.

Before: saved_videos had one full row per (user, course, module item), so classmates duplicated the same
URL, title and size. After: saved_video_items has one row per (course, module item); saved_videos keeps
(user_id, item_id). Existing saved_videos ids are kept, so /saved-videos/{id} links keep working.
When classmates' rows disagree, the row that has a media URL (then the most recently resolved) wins.
PostgreSQL only (production); tests build the schema with create_all.
"""
from alembic import op
import sqlalchemy as sa

from app.db.base import UtcDateTime

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None

ITEM_COLUMNS = ("lms_course_id, module_item_id, content_id, course_name, module_title, module_position, item_position, "
                "title, media_url, file_name, duration, size, resolve_error, resolved_at, resolve_failed_at")
MOVED = ("course_name", "module_title", "module_position", "item_position", "title", "content_id", "media_url",
         "file_name", "duration", "size", "resolve_error", "resolved_at", "resolve_failed_at")


def upgrade():
    op.create_table(
        "saved_video_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lms_course_id", sa.Integer(), nullable=False),
        sa.Column("module_item_id", sa.Integer(), nullable=False),
        sa.Column("content_id", sa.String(length=64), nullable=False),
        sa.Column("course_name", sa.String(length=255), nullable=False),
        sa.Column("module_title", sa.String(length=255), nullable=False),
        sa.Column("module_position", sa.Integer(), nullable=False),
        sa.Column("item_position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("media_url", sa.Text(), nullable=True),
        sa.Column("file_name", sa.String(length=500), nullable=False),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("resolve_error", sa.Text(), nullable=True),
        sa.Column("resolved_at", UtcDateTime(timezone=True), nullable=True),
        sa.Column("resolve_failed_at", UtcDateTime(timezone=True), nullable=True),
        sa.Column("created_at", UtcDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lms_course_id", "module_item_id"),
    )
    op.create_index("ix_saved_video_items_content_id", "saved_video_items", ["content_id"], unique=False)
    op.execute(f"""
        INSERT INTO saved_video_items ({ITEM_COLUMNS}, created_at)
        SELECT {ITEM_COLUMNS}, first_created FROM (
            SELECT s.*,
                   ROW_NUMBER() OVER (PARTITION BY lms_course_id, module_item_id
                                      ORDER BY (media_url IS NULL), (resolved_at IS NULL), resolved_at DESC, id) AS rn,
                   MIN(created_at) OVER (PARTITION BY lms_course_id, module_item_id) AS first_created
            FROM saved_videos s
        ) t WHERE rn = 1
    """)
    op.add_column("saved_videos", sa.Column("item_id", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE saved_videos s SET item_id = i.id FROM saved_video_items i
        WHERE i.lms_course_id = s.lms_course_id AND i.module_item_id = s.module_item_id
    """)
    op.alter_column("saved_videos", "item_id", nullable=False)
    op.create_foreign_key("saved_videos_item_id_fkey", "saved_videos", "saved_video_items", ["item_id"], ["id"],
                          ondelete="CASCADE")
    op.create_index("ix_saved_videos_item_id", "saved_videos", ["item_id"], unique=False)
    op.drop_constraint("saved_videos_user_id_lms_course_id_module_item_id_key", "saved_videos", type_="unique")
    op.create_unique_constraint("saved_videos_user_id_item_id_key", "saved_videos", ["user_id", "item_id"])
    for column in (*MOVED, "lms_course_id", "module_item_id"):
        op.drop_column("saved_videos", column)


def downgrade():
    op.add_column("saved_videos", sa.Column("lms_course_id", sa.Integer(), nullable=True))
    op.add_column("saved_videos", sa.Column("module_item_id", sa.Integer(), nullable=True))
    op.add_column("saved_videos", sa.Column("course_name", sa.String(length=255), nullable=True))
    op.add_column("saved_videos", sa.Column("module_title", sa.String(length=255), nullable=True))
    op.add_column("saved_videos", sa.Column("module_position", sa.Integer(), nullable=True))
    op.add_column("saved_videos", sa.Column("item_position", sa.Integer(), nullable=True))
    op.add_column("saved_videos", sa.Column("title", sa.String(length=500), nullable=True))
    op.add_column("saved_videos", sa.Column("content_id", sa.String(length=64), nullable=True))
    op.add_column("saved_videos", sa.Column("media_url", sa.Text(), nullable=True))
    op.add_column("saved_videos", sa.Column("file_name", sa.String(length=500), nullable=True))
    op.add_column("saved_videos", sa.Column("duration", sa.Float(), nullable=True))
    op.add_column("saved_videos", sa.Column("size", sa.BigInteger(), nullable=True))
    op.add_column("saved_videos", sa.Column("resolve_error", sa.Text(), nullable=True))
    op.add_column("saved_videos", sa.Column("resolved_at", UtcDateTime(timezone=True), nullable=True))
    op.add_column("saved_videos", sa.Column("resolve_failed_at", UtcDateTime(timezone=True), nullable=True))
    sets = ", ".join(f"{c} = i.{c}" for c in ("lms_course_id", "module_item_id", *MOVED))
    op.execute(f"UPDATE saved_videos s SET {sets} FROM saved_video_items i WHERE i.id = s.item_id")
    for column in ("lms_course_id", "module_item_id", "course_name", "module_title", "module_position", "item_position",
                   "title", "content_id", "file_name"):
        op.alter_column("saved_videos", column, nullable=False)
    op.drop_constraint("saved_videos_user_id_item_id_key", "saved_videos", type_="unique")
    op.create_unique_constraint("saved_videos_user_id_lms_course_id_module_item_id_key", "saved_videos",
                                ["user_id", "lms_course_id", "module_item_id"])
    op.drop_index("ix_saved_videos_item_id", table_name="saved_videos")
    op.drop_constraint("saved_videos_item_id_fkey", "saved_videos", type_="foreignkey")
    op.drop_column("saved_videos", "item_id")
    op.drop_index("ix_saved_video_items_content_id", table_name="saved_video_items")
    op.drop_table("saved_video_items")
