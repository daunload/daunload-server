"""Encrypted optional Canvas personal token and credential write version."""
from alembic import op
import sqlalchemy as sa

revision = "f6b7c8d9e0a1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("lms_credentials") as batch:
        batch.add_column(sa.Column("personal_token_enc", sa.Text(), nullable=True))
        batch.add_column(sa.Column("personal_token_status", sa.String(16), nullable=False, server_default="none"))
        batch.add_column(sa.Column("personal_token_registered_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("credential_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("lms_credentials") as batch:
        for name in ("credential_version", "personal_token_registered_at", "personal_token_status", "personal_token_enc"):
            batch.drop_column(name)
