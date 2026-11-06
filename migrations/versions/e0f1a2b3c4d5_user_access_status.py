"""Admin approval status for users. Everyone starts at 'none' (admins are treated as approved in code)."""
from alembic import op
import sqlalchemy as sa

revision = "e0f1a2b3c4d5"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("access_status", sa.String(16), server_default="none", nullable=False))
    op.add_column("users", sa.Column("access_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("access_decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("access_decided_by", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("access_reject_count", sa.Integer(), server_default="0", nullable=False))


def downgrade():
    op.drop_column("users", "access_reject_count")
    op.drop_column("users", "access_decided_by")
    op.drop_column("users", "access_decided_at")
    op.drop_column("users", "access_requested_at")
    op.drop_column("users", "access_status")
