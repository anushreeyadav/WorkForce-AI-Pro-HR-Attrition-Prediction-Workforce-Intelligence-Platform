"""token revocation: revoked_tokens table and users.token_version

Revision ID: c4e7a91d3f20
Revises: a0bd1c34f49c
Create Date: 2026-10-27 17:00:00.000000

Written by hand and reviewed (no MySQL connection). Additive only:
- new table revoked_tokens (jti primary key, user_id FK users ON DELETE CASCADE, expires_at, revoked_at), indexed on
  user_id and expires_at. It holds token ids, never tokens.
- new column users.token_version INTEGER NOT NULL DEFAULT 0. The server default fills existing rows, and tokens issued
  before this revision (no "ver" claim) are read as version 0, so existing sessions keep working until they expire.
Downgrade drops both. The only data lost is the list of revoked tokens and the session versions; after a downgrade,
tokens revoked by logout become usable again until they expire (at most ACCESS_TOKEN_EXPIRE_MINUTES).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e7a91d3f20'
down_revision: Union[str, Sequence[str], None] = 'a0bd1c34f49c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'revoked_tokens',
        sa.Column('jti', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_revoked_tokens_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('jti', name=op.f('pk_revoked_tokens')),
    )
    op.create_index(op.f('ix_revoked_tokens_user_id'), 'revoked_tokens', ['user_id'], unique=False)
    op.create_index(op.f('ix_revoked_tokens_expires_at'), 'revoked_tokens', ['expires_at'], unique=False)
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('token_version', sa.Integer(), nullable=False, server_default=sa.text('0')))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users') as batch:
        batch.drop_column('token_version')
    op.drop_table('revoked_tokens')
