"""Server-managed Canvas personal token: Canvas token id, expiry and managed flag."""
from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "f6b7c8d9e0a1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("lms_credentials") as batch:
        batch.add_column(sa.Column("personal_token_managed", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("personal_token_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("personal_token_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table("lms_credentials") as batch:
        for name in ("personal_token_expires_at", "personal_token_id", "personal_token_managed"):
            batch.drop_column(name)
