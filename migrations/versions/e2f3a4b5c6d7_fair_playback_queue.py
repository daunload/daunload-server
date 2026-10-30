"""Durable round-robin playback queue and one active batch per user."""
from alembic import op
import sqlalchemy as sa

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    # Fail visibly on legacy duplicates; do not discard users' queued work.
    duplicates = op.get_bind().execute(sa.text(
        "SELECT user_id FROM playback_batches WHERE status IN ('queued','running') "
        "GROUP BY user_id HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicates:
        raise RuntimeError("중복 활성 재생 묶음을 먼저 취소하세요: user_id=" + str(duplicates[0]))
    op.add_column("playback_batches", sa.Column("queue_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE playback_batches SET queue_at = created_at")
    op.alter_column("playback_batches", "queue_at", nullable=False)
    op.create_index("ix_playback_batches_queue_at", "playback_batches", ["queue_at"])
    op.create_index("uq_playback_active_user", "playback_batches", ["user_id"], unique=True,
                    postgresql_where=sa.text("status IN ('queued','running')"),
                    sqlite_where=sa.text("status IN ('queued','running')"))


def downgrade():
    op.drop_index("uq_playback_active_user", table_name="playback_batches")
    op.drop_index("ix_playback_batches_queue_at", table_name="playback_batches")
    op.drop_column("playback_batches", "queue_at")
