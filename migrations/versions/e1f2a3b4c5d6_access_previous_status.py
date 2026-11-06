"""Remember the status a user had just before the latest access request."""
from alembic import op
import sqlalchemy as sa

revision = "e1f2a3b4c5d6"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("access_previous_status", sa.String(16), nullable=True))


def downgrade():
    op.drop_column("users", "access_previous_status")
