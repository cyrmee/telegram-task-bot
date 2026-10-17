"""tenant scoped foreign keys

Defence in depth for tenancy: a task's project and assignee, a
notification's recipient and an event's task must be in the same workspace
as the row pointing at them. Enforced with composite (workspace_id, id) FKs.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint(op.f('uq_members_workspace_id_id'), 'members', ['workspace_id', 'id'])
    op.create_unique_constraint(op.f('uq_projects_workspace_id_id'), 'projects', ['workspace_id', 'id'])
    op.create_unique_constraint(op.f('uq_tasks_workspace_id_id'), 'tasks', ['workspace_id', 'id'])
    op.drop_constraint(op.f('fk_notifications_member_id_members'), 'notifications', type_='foreignkey')
    op.create_foreign_key(op.f('fk_notifications_workspace_id_members'), 'notifications', 'members', ['workspace_id', 'member_id'], ['workspace_id', 'id'], ondelete='CASCADE')
    op.drop_constraint(op.f('fk_task_events_task_id_tasks'), 'task_events', type_='foreignkey')
    op.create_foreign_key(op.f('fk_task_events_workspace_id_tasks'), 'task_events', 'tasks', ['workspace_id', 'task_id'], ['workspace_id', 'id'], ondelete='CASCADE')
    op.drop_constraint(op.f('fk_tasks_assignee_member_id_members'), 'tasks', type_='foreignkey')
    op.drop_constraint(op.f('fk_tasks_project_id_projects'), 'tasks', type_='foreignkey')
    op.create_foreign_key(op.f('fk_tasks_workspace_id_projects'), 'tasks', 'projects', ['workspace_id', 'project_id'], ['workspace_id', 'id'])
    op.create_foreign_key(op.f('fk_tasks_workspace_id_members'), 'tasks', 'members', ['workspace_id', 'assignee_member_id'], ['workspace_id', 'id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_tasks_workspace_id_members'), 'tasks', type_='foreignkey')
    op.drop_constraint(op.f('fk_tasks_workspace_id_projects'), 'tasks', type_='foreignkey')
    op.create_foreign_key(op.f('fk_tasks_project_id_projects'), 'tasks', 'projects', ['project_id'], ['id'])
    op.create_foreign_key(op.f('fk_tasks_assignee_member_id_members'), 'tasks', 'members', ['assignee_member_id'], ['id'])
    op.drop_constraint(op.f('fk_task_events_workspace_id_tasks'), 'task_events', type_='foreignkey')
    op.create_foreign_key(op.f('fk_task_events_task_id_tasks'), 'task_events', 'tasks', ['task_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint(op.f('fk_notifications_workspace_id_members'), 'notifications', type_='foreignkey')
    op.create_foreign_key(op.f('fk_notifications_member_id_members'), 'notifications', 'members', ['member_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint(op.f('uq_tasks_workspace_id_id'), 'tasks', type_='unique')
    op.drop_constraint(op.f('uq_projects_workspace_id_id'), 'projects', type_='unique')
    op.drop_constraint(op.f('uq_members_workspace_id_id'), 'members', type_='unique')
