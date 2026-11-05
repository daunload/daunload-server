"""Daily activity counts and error timestamps for the admin dashboard."""
from alembic import op
import sqlalchemy as sa

revision = "c8d9e0f1a2b3"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_daily_activity",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("logins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("downloads", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_user_daily_activity_day", "user_daily_activity", ["day"])
    op.add_column("lms_credentials", sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("saved_videos", sa.Column("resolve_failed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("saved_videos", "resolve_failed_at")
    op.drop_column("lms_credentials", "last_error_at")
    op.drop_index("ix_user_daily_activity_day", table_name="user_daily_activity")
    op.drop_table("user_daily_activity")
