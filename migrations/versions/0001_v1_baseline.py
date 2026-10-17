"""v1 baseline

Creates the v1 multi-tenant schema (see docs/data-model.md).

Pre-Alembic databases (the old create_all()/migrate_*() bot and the Prisma
dashboard) have tables with the same names as v1 ones. Rather than dropping
them here, they're moved into a `legacy` schema so nothing is lost until
0002 drops it. better-auth tables (user, session, account, verification)
are never touched.

Revision ID: 0001
Revises:
Create Date: 2026-10-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_TABLES = (
    "Member", "Project", "Task", "Workspace",  # Prisma (dashboard)
    "members", "projects", "reminders", "task_assignments", "tasks", "users", "workspaces",  # old bot
)


def _move_legacy_tables_aside() -> None:
    bind = op.get_bind()
    present = [
        t for t in LEGACY_TABLES
        if bind.execute(sa.text("SELECT to_regclass(:n)"), {"n": f'public."{t}"'}).scalar() is not None
    ]
    if not present:
        return
    op.execute("CREATE SCHEMA IF NOT EXISTS legacy")
    for t in present:
        op.execute(f'ALTER TABLE public."{t}" SET SCHEMA legacy')
    # Old enum type from the bot's tasks.status column travels with it.
    op.execute(
        "DO $$ BEGIN IF to_regtype('public.taskstatus') IS NOT NULL "
        "THEN ALTER TYPE public.taskstatus SET SCHEMA legacy; END IF; END $$"
    )


def _restore_legacy_tables() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT to_regnamespace('legacy')")).scalar() is None:
        return
    for t in LEGACY_TABLES:
        op.execute(
            f"DO $$ BEGIN IF to_regclass('legacy.\"{t}\"') IS NOT NULL "
            f"THEN ALTER TABLE legacy.\"{t}\" SET SCHEMA public; END IF; END $$"
        )
    op.execute(
        "DO $$ BEGIN IF to_regtype('legacy.taskstatus') IS NOT NULL "
        "THEN ALTER TYPE legacy.taskstatus SET SCHEMA public; END IF; END $$"
    )
    op.execute("DROP SCHEMA IF EXISTS legacy")  # no CASCADE: fails loudly if anything else is in it


def upgrade() -> None:
    """Upgrade schema."""
    _move_legacy_tables_aside()

    op.create_table('telegram_users',
    sa.Column('telegram_user_id', sa.BigInteger(), autoincrement=False, nullable=False),
    sa.Column('username', sa.String(length=64), nullable=True),
    sa.Column('first_name', sa.String(length=128), nullable=True),
    sa.Column('last_name', sa.String(length=128), nullable=True),
    sa.Column('language_code', sa.String(length=16), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('telegram_user_id', name=op.f('pk_telegram_users'))
    )
    op.create_index(op.f('ix_telegram_users_username'), 'telegram_users', ['username'], unique=False)
    op.create_table('workspaces',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('owner_user_id', sa.Text(), nullable=False),
    sa.Column('timezone', sa.String(length=64), server_default='UTC', nullable=False),
    sa.Column('reminder_lead_hours', sa.Integer(), server_default='24', nullable=False),
    sa.Column('next_task_number', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('next_task_number > 0', name=op.f('ck_workspaces_next_task_number_positive')),
    sa.CheckConstraint('reminder_lead_hours BETWEEN 0 AND 720', name=op.f('ck_workspaces_reminder_lead_hours_range')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workspaces'))
    )
    op.create_index(op.f('ix_workspaces_owner_user_id'), 'workspaces', ['owner_user_id'], unique=False)
    op.create_table('members',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.Text(), nullable=True),
    sa.Column('telegram_user_id', sa.BigInteger(), nullable=True),
    sa.Column('invited_username', sa.String(length=64), nullable=True),
    sa.Column('role', sa.Enum('manager', 'member', name='member_role', native_enum=False, create_constraint=True, length=32), server_default='member', nullable=False),
    sa.Column('status', sa.Enum('pending', 'active', 'unreachable', 'removed', name='member_status', native_enum=False, create_constraint=True, length=32), server_default='active', nullable=False),
    sa.Column('private_chat_id', sa.BigInteger(), nullable=True),
    sa.Column('invite_id', sa.UUID(), nullable=True),
    sa.Column('joined_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('user_id IS NOT NULL OR telegram_user_id IS NOT NULL OR invited_username IS NOT NULL', name=op.f('ck_members_has_identity')),
    sa.ForeignKeyConstraint(['telegram_user_id'], ['telegram_users.telegram_user_id'], name=op.f('fk_members_telegram_user_id_telegram_users')),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_members_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_members')),
    sa.UniqueConstraint('workspace_id', 'telegram_user_id', name=op.f('uq_members_workspace_id_telegram_user_id')),
    sa.UniqueConstraint('workspace_id', 'user_id', name=op.f('uq_members_workspace_id_user_id'))
    )
    op.create_index(op.f('ix_members_telegram_user_id'), 'members', ['telegram_user_id'], unique=False)
    op.create_index(op.f('ix_members_user_id'), 'members', ['user_id'], unique=False)
    op.create_index(op.f('ix_members_workspace_id'), 'members', ['workspace_id'], unique=False)
    op.create_table('workspace_bots',
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('telegram_bot_id', sa.BigInteger(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('token_encrypted', sa.Text(), nullable=False),
    sa.Column('webhook_secret', sa.String(length=256), nullable=False),
    sa.Column('status', sa.Enum('active', 'invalid', 'disconnected', name='bot_status', native_enum=False, create_constraint=True, length=32), server_default='active', nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('connected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_workspace_bots_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('workspace_id', name=op.f('pk_workspace_bots'))
    )
    op.create_index('uq_workspace_bots_telegram_bot_id_live', 'workspace_bots', ['telegram_bot_id'], unique=True, postgresql_where=sa.text("status <> 'disconnected'"))
    op.create_table('invites',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('token', sa.String(length=64), nullable=False),
    sa.Column('role', sa.Enum('manager', 'member', name='invite_role', native_enum=False, create_constraint=True, length=32), server_default='member', nullable=False),
    sa.Column('created_by_member_id', sa.UUID(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('max_uses', sa.Integer(), server_default='1', nullable=True),
    sa.Column('use_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('max_uses IS NULL OR max_uses > 0', name=op.f('ck_invites_max_uses_positive')),
    sa.CheckConstraint('use_count >= 0', name=op.f('ck_invites_use_count_non_negative')),
    sa.ForeignKeyConstraint(['created_by_member_id'], ['members.id'], name=op.f('fk_invites_created_by_member_id_members'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_invites_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invites')),
    sa.UniqueConstraint('token', name=op.f('uq_invites_token'))
    )
    op.create_index(op.f('ix_invites_workspace_id'), 'invites', ['workspace_id'], unique=False)
    # members <-> invites is circular, so this FK is added once both tables exist.
    op.create_foreign_key(op.f('fk_members_invite_id_invites'), 'members', 'invites', ['invite_id'], ['id'], ondelete='SET NULL')
    op.create_table('projects',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('active', 'archived', name='project_status', native_enum=False, create_constraint=True, length=32), server_default='active', nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by_member_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by_member_id'], ['members.id'], name=op.f('fk_projects_created_by_member_id_members'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_projects_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_projects'))
    )
    op.create_index(op.f('ix_projects_workspace_id_status'), 'projects', ['workspace_id', 'status'], unique=False)
    op.create_table('tasks',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('assignee_member_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.Enum('pending', 'completed', name='task_status', native_enum=False, create_constraint=True, length=32), server_default='pending', nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_via', sa.Enum('telegram', 'dashboard', 'system', name='completed_via', native_enum=False, create_constraint=True, length=32), nullable=True),
    sa.Column('completed_by_member_id', sa.UUID(), nullable=True),
    sa.Column('created_by_member_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'completed') = (completed_at IS NOT NULL)", name=op.f('ck_tasks_completed_at_matches_status')),
    sa.ForeignKeyConstraint(['assignee_member_id'], ['members.id'], name=op.f('fk_tasks_assignee_member_id_members')),
    sa.ForeignKeyConstraint(['completed_by_member_id'], ['members.id'], name=op.f('fk_tasks_completed_by_member_id_members'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_member_id'], ['members.id'], name=op.f('fk_tasks_created_by_member_id_members'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_tasks_project_id_projects')),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_tasks_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tasks')),
    sa.UniqueConstraint('workspace_id', 'number', name=op.f('uq_tasks_workspace_id_number'))
    )
    op.create_index(op.f('ix_tasks_project_id_status'), 'tasks', ['project_id', 'status'], unique=False)
    op.create_index(op.f('ix_tasks_workspace_id_assignee_member_id_status'), 'tasks', ['workspace_id', 'assignee_member_id', 'status'], unique=False)
    op.create_index(op.f('ix_tasks_workspace_id_status_due_at'), 'tasks', ['workspace_id', 'status', 'due_at'], unique=False)
    op.create_table('notifications',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=True),
    sa.Column('member_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.Enum('assigned', 'unassigned', 'task_updated', 'task_deleted', 'reminder', 'overdue', 'completed_manager', 'digest', 'invite', 'welcome', name='notification_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('status', sa.Enum('queued', 'sent', 'failed', 'cancelled', name='notification_status', native_enum=False, create_constraint=True, length=32), server_default='queued', nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('telegram_chat_id', sa.BigInteger(), nullable=True),
    sa.Column('telegram_message_id', sa.BigInteger(), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], name=op.f('fk_notifications_member_id_members'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_notifications_task_id_tasks'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_notifications_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_notifications_idempotency_key'))
    )
    op.create_index('ix_notifications_due', 'notifications', ['next_attempt_at'], unique=False, postgresql_where=sa.text("status = 'queued'"))
    op.create_index(op.f('ix_notifications_member_id'), 'notifications', ['member_id'], unique=False)
    op.create_index(op.f('ix_notifications_task_id'), 'notifications', ['task_id'], unique=False)
    op.create_index(op.f('ix_notifications_workspace_id'), 'notifications', ['workspace_id'], unique=False)
    op.create_table('task_events',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.Enum('created', 'updated', 'reassigned', 'due_changed', 'completed', 'reopened', name='task_event_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('source', sa.Enum('telegram', 'dashboard', 'system', name='task_event_source', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('actor_member_id', sa.UUID(), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_member_id'], ['members.id'], name=op.f('fk_task_events_actor_member_id_members'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_task_events_task_id_tasks'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_task_events_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_task_events'))
    )
    op.create_index(op.f('ix_task_events_task_id_created_at'), 'task_events', ['task_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_task_events_workspace_id'), 'task_events', ['workspace_id'], unique=False)
    op.create_table('task_reminders',
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('offset_minutes', sa.Integer(), autoincrement=False, nullable=False),
    sa.CheckConstraint('offset_minutes > 0', name=op.f('ck_task_reminders_offset_minutes_positive')),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_task_reminders_task_id_tasks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('task_id', 'offset_minutes', name=op.f('pk_task_reminders'))
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('task_reminders')
    op.drop_index(op.f('ix_task_events_workspace_id'), table_name='task_events')
    op.drop_index(op.f('ix_task_events_task_id_created_at'), table_name='task_events')
    op.drop_table('task_events')
    op.drop_index(op.f('ix_notifications_workspace_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_task_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_member_id'), table_name='notifications')
    op.drop_index('ix_notifications_due', table_name='notifications', postgresql_where=sa.text("status = 'queued'"))
    op.drop_table('notifications')
    op.drop_index(op.f('ix_tasks_workspace_id_status_due_at'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_workspace_id_assignee_member_id_status'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_project_id_status'), table_name='tasks')
    op.drop_table('tasks')
    op.drop_index(op.f('ix_projects_workspace_id_status'), table_name='projects')
    op.drop_table('projects')
    op.drop_index(op.f('ix_invites_workspace_id'), table_name='invites')
    op.drop_constraint(op.f('fk_members_invite_id_invites'), 'members', type_='foreignkey')
    op.drop_table('invites')
    op.drop_index('uq_workspace_bots_telegram_bot_id_live', table_name='workspace_bots', postgresql_where=sa.text("status <> 'disconnected'"))
    op.drop_table('workspace_bots')
    op.drop_index(op.f('ix_members_workspace_id'), table_name='members')
    op.drop_index(op.f('ix_members_user_id'), table_name='members')
    op.drop_index(op.f('ix_members_telegram_user_id'), table_name='members')
    op.drop_table('members')
    op.drop_index(op.f('ix_workspaces_owner_user_id'), table_name='workspaces')
    op.drop_table('workspaces')
    op.drop_index(op.f('ix_telegram_users_username'), table_name='telegram_users')
    op.drop_table('telegram_users')

    _restore_legacy_tables()
