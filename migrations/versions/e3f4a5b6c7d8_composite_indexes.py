"""Add composite indexes for playback queue, deadline reminders, and log pruning.

Revision ID: e3f4a5b6c7d8
Revises: d0e1f2a3b4c5
"""

from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_todos_user_active_deadline", "todos", ["user_id", "active", "deadline"])
    op.create_index("ix_playback_jobs_batch_status", "playback_jobs", ["batch_id", "status"])
    op.create_index("ix_playback_jobs_user_status_finished", "playback_jobs", ["user_id", "status", "finished_at"])
    op.create_index("ix_notification_log_created_at", "notification_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_notification_log_created_at", table_name="notification_log")
    op.drop_index("ix_playback_jobs_user_status_finished", table_name="playback_jobs")
    op.drop_index("ix_playback_jobs_batch_status", table_name="playback_jobs")
    op.drop_index("ix_todos_user_active_deadline", table_name="todos")
