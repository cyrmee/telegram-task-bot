"""onboarding state and member link invites

workspaces.onboarding_dismissed_at / onboarding_skipped_steps back the C3
checklist; invites.member_id makes an invite link Telegram to an existing
member (manager self-linking) instead of creating one.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('invites', sa.Column('member_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_invites_member_id'), 'invites', ['member_id'], unique=False)
    op.create_foreign_key(op.f('fk_invites_workspace_id_members'), 'invites', 'members', ['workspace_id', 'member_id'], ['workspace_id', 'id'], ondelete='CASCADE')
    op.add_column('workspaces', sa.Column('onboarding_dismissed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('workspaces', sa.Column('onboarding_skipped_steps', postgresql.ARRAY(sa.String(length=32)), server_default='{}', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('workspaces', 'onboarding_skipped_steps')
    op.drop_column('workspaces', 'onboarding_dismissed_at')
    op.drop_constraint(op.f('fk_invites_workspace_id_members'), 'invites', type_='foreignkey')
    op.drop_index(op.f('ix_invites_member_id'), table_name='invites')
    op.drop_column('invites', 'member_id')
