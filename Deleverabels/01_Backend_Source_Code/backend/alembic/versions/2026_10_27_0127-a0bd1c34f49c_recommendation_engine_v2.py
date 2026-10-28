"""recommendation engine v2: new enum values and nullable rules-v2 columns on recommendations

Revision ID: a0bd1c34f49c
Revises: b647981f350c
Create Date: 2026-10-27 01:27:07.810660

Written by hand and reviewed (no MySQL connection). Changes ONE existing table, additively:
- enum values appended: category FOLLOW_UP_REVIEW, GOAL_SUPPORT, DATA_REFRESH; priority CRITICAL; status SUPERSEDED
  (existing values and their order are unchanged, so existing rows stay valid)
- nullable columns task_id (FK tasks, RESTRICT, indexed), rank_score (CHECK 0-100), priority_reasons (JSON),
  evidence_fingerprint, superseded_by_id (FK recommendations, RESTRICT)
Uses batch mode so SQLite (tests) can recreate the table; on MySQL the same operations are plain ALTER statements.
Downgrade refuses to run while any row uses a rules-v2-only value or column, instead of silently losing data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a0bd1c34f49c'
down_revision: Union[str, Sequence[str], None] = 'b647981f350c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_CATEGORY = ('SUPPORT_CHECK_IN', 'WORKLOAD_REVIEW', 'CAREER_DEVELOPMENT', 'DEPARTMENT_RETENTION_REVIEW',
                'WORKFORCE_TREND_REVIEW', 'DATA_QUALITY')
NEW_CATEGORY = OLD_CATEGORY + ('FOLLOW_UP_REVIEW', 'GOAL_SUPPORT', 'DATA_REFRESH')
OLD_PRIORITY = ('LOW', 'MEDIUM', 'HIGH')
NEW_PRIORITY = OLD_PRIORITY + ('CRITICAL',)
OLD_STATUS = ('AVAILABLE', 'ACCEPTED', 'DISMISSED', 'COMPLETED')
NEW_STATUS = OLD_STATUS + ('SUPERSEDED',)


def _enums(category, priority, status):
    return (sa.Enum(*category, name='recommendation_category'), sa.Enum(*priority, name='recommendation_priority'),
            sa.Enum(*status, name='recommendation_status'))


def upgrade() -> None:
    """Upgrade schema."""
    old = _enums(OLD_CATEGORY, OLD_PRIORITY, OLD_STATUS)
    new = _enums(NEW_CATEGORY, NEW_PRIORITY, NEW_STATUS)
    with op.batch_alter_table('recommendations') as batch:
        for column, before, after in zip(('category', 'priority', 'status'), old, new):
            batch.alter_column(column, existing_type=before, type_=after, existing_nullable=False)
        batch.add_column(sa.Column('task_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('rank_score', sa.SmallInteger(), nullable=True))
        batch.add_column(sa.Column('priority_reasons', sa.JSON(), nullable=True))
        batch.add_column(sa.Column('evidence_fingerprint', sa.String(length=64), nullable=True))
        batch.add_column(sa.Column('superseded_by_id', sa.Integer(), nullable=True))
        # Index before the foreign key, so MySQL reuses it instead of creating a second implicit index.
        batch.create_index(op.f('ix_recommendations_task_id'), ['task_id'], unique=False)
        batch.create_foreign_key(op.f('fk_recommendations_task_id_tasks'), 'tasks', ['task_id'], ['id'], ondelete='RESTRICT')
        batch.create_foreign_key(op.f('fk_recommendations_superseded_by_id_recommendations'), 'recommendations',
                                 ['superseded_by_id'], ['id'], ondelete='RESTRICT')
        batch.create_check_constraint(op.f('ck_recommendations_rank_score_range'),
                                      'rank_score IS NULL OR (rank_score >= 0 AND rank_score <= 100)')


def downgrade() -> None:
    """Downgrade schema."""
    if not op.get_context().as_sql:  # offline (--sql) rendering cannot inspect data
        in_use = op.get_bind().execute(sa.text(
            "SELECT COUNT(*) FROM recommendations WHERE rule_version = 'rules-v2' OR priority = 'CRITICAL' "
            "OR status = 'SUPERSEDED' OR category IN ('FOLLOW_UP_REVIEW', 'GOAL_SUPPORT', 'DATA_REFRESH') "
            "OR task_id IS NOT NULL")).scalar()
        if in_use:
            raise RuntimeError(f"{in_use} recommendation row(s) use rules-v2 values; archive or remove them before "
                               "downgrading below a0bd1c34f49c")
    old = _enums(OLD_CATEGORY, OLD_PRIORITY, OLD_STATUS)
    new = _enums(NEW_CATEGORY, NEW_PRIORITY, NEW_STATUS)
    with op.batch_alter_table('recommendations') as batch:
        batch.drop_constraint(op.f('ck_recommendations_rank_score_range'), type_='check')
        batch.drop_index(op.f('ix_recommendations_task_id'))
        batch.drop_constraint(op.f('fk_recommendations_superseded_by_id_recommendations'), type_='foreignkey')
        batch.drop_constraint(op.f('fk_recommendations_task_id_tasks'), type_='foreignkey')
        for column in ('superseded_by_id', 'evidence_fingerprint', 'priority_reasons', 'rank_score', 'task_id'):
            batch.drop_column(column)
        for column, before, after in zip(('category', 'priority', 'status'), new, old):
            batch.alter_column(column, existing_type=before, type_=after, existing_nullable=False)
