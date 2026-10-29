"""download records and transcripts

Revision ID: d1e2f3a4b5c6
Revises: c3d5e7f9a1b2
Create Date: 2026-10-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.db.base


revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c3d5e7f9a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('download_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('lms_course_id', sa.Integer(), nullable=False),
    sa.Column('item_key', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('first_at', app.db.base.UtcDateTime(timezone=True), nullable=False),
    sa.Column('last_at', app.db.base.UtcDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'lms_course_id', 'item_key')
    )
    op.create_index(op.f('ix_download_records_user_id'), 'download_records', ['user_id'], unique=False)
    op.create_table('transcripts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('lms_course_id', sa.Integer(), nullable=False),
    sa.Column('module_item_id', sa.Integer(), nullable=False),
    sa.Column('course_name', sa.String(length=255), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('segments', sa.JSON(), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('duration', sa.Float(), nullable=True),
    sa.Column('created_at', app.db.base.UtcDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', app.db.base.UtcDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'lms_course_id', 'module_item_id')
    )
    op.create_index(op.f('ix_transcripts_user_id'), 'transcripts', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_transcripts_user_id'), table_name='transcripts')
    op.drop_table('transcripts')
    op.drop_index(op.f('ix_download_records_user_id'), table_name='download_records')
    op.drop_table('download_records')
