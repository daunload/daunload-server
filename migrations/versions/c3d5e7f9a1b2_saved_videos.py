"""saved videos

Revision ID: c3d5e7f9a1b2
Revises: bd6ac3c21017
Create Date: 2026-10-08 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.db.base


# revision identifiers, used by Alembic.
revision: str = 'c3d5e7f9a1b2'
down_revision: Union[str, Sequence[str], None] = 'bd6ac3c21017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('saved_videos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('lms_course_id', sa.Integer(), nullable=False),
    sa.Column('course_name', sa.String(length=255), nullable=False),
    sa.Column('module_item_id', sa.Integer(), nullable=False),
    sa.Column('module_title', sa.String(length=255), nullable=False),
    sa.Column('module_position', sa.Integer(), nullable=False),
    sa.Column('item_position', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('content_id', sa.String(length=64), nullable=False),
    sa.Column('media_url', sa.Text(), nullable=True),
    sa.Column('file_name', sa.String(length=500), nullable=False),
    sa.Column('duration', sa.Float(), nullable=True),
    sa.Column('size', sa.BigInteger(), nullable=True),
    sa.Column('resolve_error', sa.Text(), nullable=True),
    sa.Column('resolved_at', app.db.base.UtcDateTime(timezone=True), nullable=True),
    sa.Column('created_at', app.db.base.UtcDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'lms_course_id', 'module_item_id')
    )
    op.create_index(op.f('ix_saved_videos_user_id'), 'saved_videos', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_saved_videos_user_id'), table_name='saved_videos')
    op.drop_table('saved_videos')
