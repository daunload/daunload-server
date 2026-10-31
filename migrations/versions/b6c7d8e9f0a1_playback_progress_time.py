"""Record when playback position last advanced."""
from alembic import op
import sqlalchemy as sa
from app.db.base import UtcDateTime

revision = "b6c7d8e9f0a1"
down_revision = "a5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("playback_jobs", sa.Column("progress_updated_at", UtcDateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("playback_jobs", "progress_updated_at")
