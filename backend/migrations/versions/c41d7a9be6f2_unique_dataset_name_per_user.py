"""unique dataset name per user

Revision ID: c41d7a9be6f2
Revises: 90513910f073
Create Date: 2026-09-01 10:12:44.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c41d7a9be6f2'
down_revision: Union[str, Sequence[str], None] = '90513910f073'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Names were free-form until now, so an existing database may already hold
    # duplicates. Suffix every duplicate but the oldest ("Q1 books (2)", ...)
    # before the index goes on, otherwise the CREATE INDEX itself fails.
    op.execute(
        sa.text(
            """
            UPDATE datasets AS d
            SET name = left(d.name, 249) || ' (' || ranked.rank || ')'
            FROM (
                SELECT id,
                       row_number() OVER (PARTITION BY user_id, name ORDER BY created_at, id) AS rank
                FROM datasets
            ) AS ranked
            WHERE ranked.id = d.id AND ranked.rank > 1
            """
        )
    )
    op.create_index("ix_datasets_user_name", "datasets", ["user_id", "name"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_datasets_user_name", table_name="datasets")
