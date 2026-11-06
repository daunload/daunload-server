"""Stop storing school passwords: drop the password hash and the encrypted password.

Sessions (session_enc) are kept. Users whose stored SSO token has expired are marked
invalid on their next LMS access and asked to log in again. The dropped values cannot be
restored by downgrade.
"""
from alembic import op
import sqlalchemy as sa

revision = "f5a6b7c8d9e0"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("password_hash")
    with op.batch_alter_table("lms_credentials") as batch:
        batch.drop_column("password_enc")


def downgrade():
    with op.batch_alter_table("lms_credentials") as batch:
        batch.add_column(sa.Column("password_enc", sa.Text(), nullable=True))
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("password_hash", sa.String(255), nullable=True))
