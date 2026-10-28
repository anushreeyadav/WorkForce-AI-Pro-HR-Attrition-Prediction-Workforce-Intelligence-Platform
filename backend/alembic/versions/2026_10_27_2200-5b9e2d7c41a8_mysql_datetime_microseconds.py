"""mysql datetime microseconds: every timestamp column becomes DATETIME(6) on MySQL

Revision ID: 5b9e2d7c41a8
Revises: c4e7a91d3f20
Create Date: 2026-10-27 22:00:00

Why: MySQL's plain DATETIME keeps whole seconds and rounds the fraction, so a stored time can be up to 0.5 s later
than the moment it records, and same-second events compare equal. The application and its tests assume microsecond
timestamps (activity-feed cursors, ordering, equality after reload), which SQLite already provides. Found by running
the full test suite against a disposable MySQL 8.0 database (Sub-prompt 32).

Effect:
- MySQL: one ALTER TABLE ... MODIFY per table for the 90 timestamp columns listed below (nullability unchanged; no
  defaults exist on these columns). Each ALTER rebuilds its table (ALGORITHM=COPY): on large tables this takes time
  and locks writes, so run it in a maintenance window after a verified backup. Existing values keep their seconds.
- SQLite and other databases: no-op (DATETIME values already keep microseconds there).
- Downgrade (MySQL): back to DATETIME; fractions are rounded to whole seconds (data change, not data loss of rows).
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '5b9e2d7c41a8'
down_revision: Union[str, Sequence[str], None] = 'c4e7a91d3f20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# table -> [(column, nullable)]: every UTCDateTime column at revision c4e7a91d3f20 (checked by tests/test_migrations.py).
TIMESTAMP_COLUMNS: dict[str, list[tuple[str, bool]]] = {
    'attendance_records': [('check_in', True), ('check_out', True), ('created_at', False), ('updated_at', False)],
    'attrition_model_versions': [('trained_at', False), ('activated_at', True), ('created_at', False), ('updated_at', False)],
    'attrition_predictions': [('predicted_at', False)],
    'audit_logs': [('created_at', False)],
    'dataset_versions': [('validation_started_at', True), ('validated_at', True), ('created_at', False), ('updated_at', False)],
    'datasets': [('archived_at', True), ('created_at', False), ('updated_at', False)],
    'departments': [('created_at', False), ('updated_at', False)],
    'employees': [('created_at', False), ('updated_at', False)],
    'intervention_events': [('created_at', False)],
    'interventions': [('started_at', True), ('completed_at', True), ('cancelled_at', True), ('created_at', False), ('updated_at', False)],
    'leave_balances': [('created_at', False), ('updated_at', False)],
    'leave_policies': [('created_at', False), ('updated_at', False)],
    'leave_requests': [('reviewed_at', True), ('cancelled_at', True), ('created_at', False), ('updated_at', False)],
    'leave_types': [('created_at', False), ('updated_at', False)],
    'notifications': [('read_at', True), ('created_at', False)],
    'payroll_periods': [('created_at', False), ('updated_at', False)],
    'payroll_profiles': [('created_at', False), ('updated_at', False)],
    'payroll_records': [('calculated_at', False), ('approved_at', True), ('paid_at', True), ('created_at', False), ('updated_at', False)],
    'performance_cycles': [('created_at', False), ('updated_at', False)],
    'performance_events': [('created_at', False)],
    'performance_goals': [('completed_at', True), ('created_at', False), ('updated_at', False)],
    'performance_reviews': [('self_submitted_at', True), ('manager_submitted_at', True), ('finalized_at', True), ('cancelled_at', True), ('created_at', False), ('updated_at', False)],
    'recommendations': [('source_data_as_of', True), ('evaluated_at', False), ('reviewed_at', True), ('created_at', False), ('updated_at', False)],
    'revoked_tokens': [('expires_at', False), ('revoked_at', False)],
    'risk_alert_escalations': [('review_due_at', False), ('created_at', False)],
    'risk_alerts': [('first_detected_at', False), ('last_observed_at', False), ('acknowledged_at', True), ('resolved_at', True), ('created_at', False), ('updated_at', False)],
    'risk_events': [('detected_at', False)],
    'salary_components': [('created_at', False), ('updated_at', False)],
    'shifts': [('created_at', False), ('updated_at', False)],
    'task_events': [('created_at', False)],
    'tasks': [('started_at', True), ('completed_at', True), ('cancelled_at', True), ('created_at', False), ('updated_at', False)],
    'users': [('last_login_at', True), ('created_at', False), ('updated_at', False)],
    'workforce_snapshots': [('captured_at', False)],
}


def _alter_all(sql_type: str) -> None:
    if op.get_context().dialect.name != 'mysql':
        return  # other databases already store microseconds
    for table, columns in TIMESTAMP_COLUMNS.items():
        modifies = ', '.join(f"MODIFY `{name}` {sql_type} {'NULL' if nullable else 'NOT NULL'}"
                             for name, nullable in columns)
        op.execute(f"ALTER TABLE `{table}` {modifies}")


def upgrade() -> None:
    """Upgrade schema."""
    _alter_all('DATETIME(6)')


def downgrade() -> None:
    """Downgrade schema."""
    _alter_all('DATETIME')
