"""login with student id instead of email

Revision ID: b7c1d2e3f4a5
Revises: f4a8ab13ad33
Create Date: 2026-10-07 19:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "f4a8ab13ad33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("student_id", sa.String(length=32), nullable=True))
    # 기존 사용자는 등록된 LMS 학번을 가져온다. 학번이 없는 계정은 로그인할 방법이 없으므로 지운다.
    op.execute(
        "UPDATE users SET student_id = (SELECT student_id FROM lms_credentials WHERE lms_credentials.user_id = users.id)"
    )
    op.execute("DELETE FROM users WHERE student_id IS NULL")
    with op.batch_alter_table("users") as batch:
        batch.alter_column("student_id", existing_type=sa.String(length=32), nullable=False)
        batch.drop_index("ix_users_email")
        batch.drop_column("email")
        batch.create_index("ix_users_student_id", ["student_id"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email", sa.String(length=255), nullable=True))
    op.execute("UPDATE users SET email = student_id || '@ssu.ac.kr'")
    with op.batch_alter_table("users") as batch:
        batch.alter_column("email", existing_type=sa.String(length=255), nullable=False)
        batch.drop_index("ix_users_student_id")
        batch.create_index("ix_users_email", ["email"], unique=True)
        batch.drop_column("student_id")
