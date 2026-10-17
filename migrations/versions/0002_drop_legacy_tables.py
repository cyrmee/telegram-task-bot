"""drop legacy tables

Drops the `legacy` schema that 0001 moved the pre-Alembic tables into.
Legacy data is not carried over; see "Legacy data" in docs/data-model.md.
To keep a copy, stop at 0001 (`alembic upgrade 0001`), export `legacy.*`,
then continue to head.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-17

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS legacy CASCADE")


def downgrade() -> None:
    # The dropped rows can't be restored; restore from a backup if needed.
    pass
