"""Allow videos without a deadline in todos and playback jobs."""
from alembic import op
import sqlalchemy as sa

revision = "d9e0f1a2b3c4"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("todos", "deadline", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column("playback_jobs", "deadline", existing_type=sa.DateTime(timezone=True), nullable=True)


def downgrade():
    # 동기화가 할 일을 다시 만들므로 지워도 된다. 재생 기록은 남기고 값만 채운다.
    op.execute("DELETE FROM todos WHERE deadline IS NULL")
    op.execute("UPDATE playback_jobs SET deadline = CURRENT_TIMESTAMP WHERE deadline IS NULL")
    op.alter_column("playback_jobs", "deadline", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("todos", "deadline", existing_type=sa.DateTime(timezone=True), nullable=False)
