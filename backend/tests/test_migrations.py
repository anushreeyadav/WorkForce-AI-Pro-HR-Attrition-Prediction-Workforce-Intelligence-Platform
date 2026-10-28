"""Migration checks run against in-memory SQLite or offline SQL only; never against MySQL. (Applying the chain to a
real MySQL server is an explicit operator step against a disposable database; see tests/mysql_guard.py.)"""

import re
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.models import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = {
    "users", "departments", "employees", "audit_logs", "shifts", "attendance_records",
    "leave_types", "leave_policies", "leave_requests", "leave_balances", "notifications",
    "salary_components", "payroll_profiles", "payroll_profile_components", "payroll_periods",
    "payroll_records", "payroll_line_items",
    "attrition_model_versions", "attrition_predictions",
    "datasets", "dataset_versions",
    "risk_alerts", "risk_events",
    "workforce_snapshots", "workforce_snapshot_departments",
    "interventions", "intervention_events",
    "recommendations",
    "risk_alert_escalations",
    "tasks", "task_events",
    "performance_cycles", "performance_goals", "performance_reviews", "performance_events",
    "revoked_tokens",
}


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini")))


def _run(direction: str, connection) -> None:
    """Apply every revision's upgrade (base -> head) or downgrade (head -> base) on the connection."""
    revisions = list(_script_directory().walk_revisions())  # head -> base
    if direction == "upgrade":
        revisions.reverse()
    with Operations.context(MigrationContext.configure(connection)):
        for revision in revisions:
            getattr(revision.module, direction)()


def test_single_linear_head() -> None:
    script = _script_directory()
    assert len(script.get_heads()) == 1
    assert script.get_base() is not None


def test_alembic_ini_has_no_database_url() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    assert config.get_main_option("sqlalchemy.url") is None


def test_upgrade_matches_models_and_downgrade_cleans_up() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _run("upgrade", connection)
        assert set(inspect(connection).get_table_names()) == EXPECTED_TABLES

        diffs = compare_metadata(
            MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata
        )
        assert diffs == []

        _run("downgrade", connection)
        assert inspect(connection).get_table_names() == []
    engine.dispose()


RECOMMENDATION_V2_ALTERS = (
    "ALTER TABLE recommendations MODIFY category ENUM('SUPPORT_CHECK_IN','WORKLOAD_REVIEW','CAREER_DEVELOPMENT',"
    "'DEPARTMENT_RETENTION_REVIEW','WORKFORCE_TREND_REVIEW','DATA_QUALITY','FOLLOW_UP_REVIEW','GOAL_SUPPORT',"
    "'DATA_REFRESH') NOT NULL;",
    "ALTER TABLE recommendations MODIFY priority ENUM('LOW','MEDIUM','HIGH','CRITICAL') NOT NULL;",
    "ALTER TABLE recommendations MODIFY status ENUM('AVAILABLE','ACCEPTED','DISMISSED','COMPLETED','SUPERSEDED') NOT NULL;",
    "ALTER TABLE recommendations ADD COLUMN task_id INTEGER;",
    "ALTER TABLE recommendations ADD COLUMN rank_score SMALLINT;",
    "ALTER TABLE recommendations ADD COLUMN priority_reasons JSON;",
    "ALTER TABLE recommendations ADD COLUMN evidence_fingerprint VARCHAR(64);",
    "ALTER TABLE recommendations ADD COLUMN superseded_by_id INTEGER;",
    "ALTER TABLE recommendations ADD CONSTRAINT fk_recommendations_task_id_tasks FOREIGN KEY(task_id) "
    "REFERENCES tasks (id) ON DELETE RESTRICT;",
    "ALTER TABLE recommendations ADD CONSTRAINT fk_recommendations_superseded_by_id_recommendations "
    "FOREIGN KEY(superseded_by_id) REFERENCES recommendations (id) ON DELETE RESTRICT;",
    "ALTER TABLE recommendations ADD CONSTRAINT ck_recommendations_rank_score_range CHECK "
    "(rank_score IS NULL OR (rank_score >= 0 AND rank_score <= 100));",
)


def test_offline_mysql_sql_creates_only_expected_objects(capsys) -> None:
    from alembic import command

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(config, "head", sql=True)
    sql = capsys.readouterr().out

    created = {line.split()[2] for line in sql.splitlines() if line.startswith("CREATE TABLE")}
    assert created == EXPECTED_TABLES | {"alembic_version"}
    # Every top-level statement must be schema creation or Alembic's own version bookkeeping.
    statements = [line for line in sql.splitlines() if line and not line[0].isspace()
                  and not line.startswith(("--", ")"))]
    allowed = ("CREATE TABLE ", "CREATE INDEX ", "INSERT INTO alembic_version ", "UPDATE alembic_version ",
               # The only changes to an existing table (66d767384f54): one nullable column and its foreign key.
               "ALTER TABLE attrition_model_versions ADD COLUMN dataset_version_id INTEGER;",
               "ALTER TABLE attrition_model_versions ADD CONSTRAINT "
               "fk_attrition_model_versions_dataset_version_id_dataset_versions FOREIGN KEY(dataset_version_id) "
               "REFERENCES dataset_versions (id) ON DELETE RESTRICT;",
               # a0bd1c34f49c: rules-v2 additions to recommendations (appended enum values, nullable columns).
               *RECOMMENDATION_V2_ALTERS,
               # c4e7a91d3f20: session version for token revocation (existing rows get 0).
               "ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0;",
               # 5b9e2d7c41a8: timestamp columns to DATETIME(6), one statement per table (checked below).
               "ALTER TABLE `")
    assert statements and all(s.startswith(allowed) for s in statements), statements
    precision = [s for s in statements if s.startswith("ALTER TABLE `")]
    assert len(precision) == len(_timestamp_columns()) and all(
        re.fullmatch(r"ALTER TABLE `\w+` (MODIFY `\w+` DATETIME\(6\) (NOT )?NULL(, )?)+;", s) for s in precision)
    assert sum(s.startswith("ALTER TABLE") for s in statements) == 2 + len(RECOMMENDATION_V2_ALTERS) + 1 + len(precision)
    assert "ENUM('ADMIN','HR','MANAGER','EMPLOYEE')" in sql
    assert "ENUM('ACTIVE','INACTIVE','ON_LEAVE')" in sql
    assert "ENUM('PRESENT','LATE','HALF_DAY','ABSENT','ON_LEAVE')" in sql
    assert "CONSTRAINT ck_attendance_records_checkout_after_checkin CHECK" in sql
    assert "CONSTRAINT uq_attendance_records_employee_id_work_date UNIQUE (employee_id, work_date)" in sql


def test_attendance_revision_only_adds_new_tables() -> None:
    script = _script_directory()
    attendance_rev = script.get_revision("319083e42c0c")
    assert attendance_rev.down_revision == "14cb546c9a5e"

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            script.get_revision("14cb546c9a5e").module.upgrade()
            before = set(inspect(connection).get_table_names())
            attendance_rev.module.upgrade()
            after = set(inspect(connection).get_table_names())
            assert after - before == {"shifts", "attendance_records"}
            attendance_rev.module.downgrade()
            assert set(inspect(connection).get_table_names()) == before
    engine.dispose()


def test_leave_revision_only_adds_new_tables() -> None:
    script = _script_directory()
    leave_rev = script.get_revision("da1930de632b")
    assert leave_rev.down_revision == "319083e42c0c"

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for rev_id in ("14cb546c9a5e", "319083e42c0c"):
                script.get_revision(rev_id).module.upgrade()
            before = set(inspect(connection).get_table_names())
            leave_rev.module.upgrade()
            after = set(inspect(connection).get_table_names())
            assert after - before == {"leave_types", "leave_policies", "leave_requests", "leave_balances", "notifications"}
            leave_rev.module.downgrade()
            assert set(inspect(connection).get_table_names()) == before
    engine.dispose()


def test_payroll_revision_only_adds_payroll_tables() -> None:
    script = _script_directory()
    payroll_rev = script.get_revision("a28868d7048a")
    assert payroll_rev.down_revision == "da1930de632b"

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for rev_id in ("14cb546c9a5e", "319083e42c0c", "da1930de632b"):
                script.get_revision(rev_id).module.upgrade()
            before = set(inspect(connection).get_table_names())
            payroll_rev.module.upgrade()
            after = set(inspect(connection).get_table_names())
            assert after - before == {
                "salary_components", "payroll_profiles", "payroll_profile_components", "payroll_periods",
                "payroll_records", "payroll_line_items",
            }
            payroll_rev.module.downgrade()
            assert set(inspect(connection).get_table_names()) == before
    engine.dispose()


def test_attrition_revision_only_adds_attrition_tables() -> None:
    script = _script_directory()
    rev = script.get_revision("371a76ea7369")
    assert rev.down_revision == "a28868d7048a"

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for rev_id in ("14cb546c9a5e", "319083e42c0c", "da1930de632b", "a28868d7048a"):
                script.get_revision(rev_id).module.upgrade()
            before = set(inspect(connection).get_table_names())
            rev.module.upgrade()
            assert set(inspect(connection).get_table_names()) - before == {"attrition_model_versions", "attrition_predictions"}
            rev.module.downgrade()
            assert set(inspect(connection).get_table_names()) == before
    engine.dispose()


def test_dataset_revision_adds_tables_and_one_nullable_column() -> None:
    script = _script_directory()
    rev = script.get_revision("66d767384f54")
    assert rev.down_revision == "371a76ea7369"

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for rev_id in ("14cb546c9a5e", "319083e42c0c", "da1930de632b", "a28868d7048a", "371a76ea7369"):
                script.get_revision(rev_id).module.upgrade()
            before_tables = set(inspect(connection).get_table_names())
            before_cols = {c["name"] for c in inspect(connection).get_columns("attrition_model_versions")}
            rev.module.upgrade()
            after = inspect(connection)
            assert set(after.get_table_names()) - before_tables == {"datasets", "dataset_versions"}
            new_cols = {c["name"]: c for c in after.get_columns("attrition_model_versions")}
            assert set(new_cols) - before_cols == {"dataset_version_id"}
            assert new_cols["dataset_version_id"]["nullable"] is True
            assert any(fk["referred_table"] == "dataset_versions"
                       for fk in after.get_foreign_keys("attrition_model_versions"))
            rev.module.downgrade()
            reverted = inspect(connection)
            assert set(reverted.get_table_names()) == before_tables
            assert {c["name"] for c in reverted.get_columns("attrition_model_versions")} == before_cols
    engine.dispose()


def test_risk_monitoring_revision_only_adds_monitoring_tables() -> None:
    script = _script_directory()
    rev = script.get_revision("03f5784d8edc")
    assert rev.down_revision == "66d767384f54"

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for rev_id in ("14cb546c9a5e", "319083e42c0c", "da1930de632b", "a28868d7048a", "371a76ea7369",
                           "66d767384f54"):
                script.get_revision(rev_id).module.upgrade()
            before = set(inspect(connection).get_table_names())
            rev.module.upgrade()
            assert set(inspect(connection).get_table_names()) - before == {"risk_alerts", "risk_events"}
            rev.module.downgrade()
            assert set(inspect(connection).get_table_names()) == before
    engine.dispose()


def test_workforce_revision_only_adds_snapshot_tables() -> None:
    script = _script_directory()
    rev = script.get_revision("70dd4dec2e60")
    assert rev.down_revision == "03f5784d8edc"

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for rev_id in ('14cb546c9a5e', '319083e42c0c', 'da1930de632b', 'a28868d7048a', '371a76ea7369', '66d767384f54', '03f5784d8edc'):
                script.get_revision(rev_id).module.upgrade()
            before = set(inspect(connection).get_table_names())
            rev.module.upgrade()
            assert set(inspect(connection).get_table_names()) - before == {"workforce_snapshots", "workforce_snapshot_departments"}
            uniques = {u["name"] for u in inspect(connection).get_unique_constraints("workforce_snapshots")}
            assert "uq_workforce_snapshots_snapshot_date" in uniques
            rev.module.downgrade()
            assert set(inspect(connection).get_table_names()) == before
    engine.dispose()


def test_intervention_revision_only_adds_intervention_tables() -> None:
    script = _script_directory()
    rev = script.get_revision("87cc35d2de25")
    assert rev.down_revision == "70dd4dec2e60"

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for rev_id in ('14cb546c9a5e', '319083e42c0c', 'da1930de632b', 'a28868d7048a', '371a76ea7369', '66d767384f54', '03f5784d8edc', '70dd4dec2e60'):
                script.get_revision(rev_id).module.upgrade()
            before = set(inspect(connection).get_table_names())
            rev.module.upgrade()
            assert set(inspect(connection).get_table_names()) - before == {"interventions", "intervention_events"}
            uniques = {u["name"] for u in inspect(connection).get_unique_constraints("interventions")}
            assert {"uq_interventions_active_alert_key", "uq_interventions_idempotency_key"} <= uniques
            rev.module.downgrade()
            assert set(inspect(connection).get_table_names()) == before
    engine.dispose()


def test_recommendation_revision_only_adds_recommendations_table() -> None:
    script = _script_directory()
    rev = script.get_revision("a6382858faa9")
    assert rev.down_revision == "87cc35d2de25"

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for rev_id in ('14cb546c9a5e', '319083e42c0c', 'da1930de632b', 'a28868d7048a', '371a76ea7369', '66d767384f54', '03f5784d8edc', '70dd4dec2e60', '87cc35d2de25'):
                script.get_revision(rev_id).module.upgrade()
            before = set(inspect(connection).get_table_names())
            rev.module.upgrade()
            assert set(inspect(connection).get_table_names()) - before == {"recommendations"}
            uniques = {u["name"] for u in inspect(connection).get_unique_constraints("recommendations")}
            assert "uq_recommendations_dedup_key" in uniques
            rev.module.downgrade()
            assert set(inspect(connection).get_table_names()) == before
    engine.dispose()


def test_escalation_revision_only_adds_escalations_table() -> None:
    script = _script_directory()
    rev = script.get_revision("0bc0f8d45e33")
    assert rev.down_revision == "a6382858faa9"

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for rev_id in ('14cb546c9a5e', '319083e42c0c', 'da1930de632b', 'a28868d7048a', '371a76ea7369', '66d767384f54', '03f5784d8edc', '70dd4dec2e60', '87cc35d2de25', 'a6382858faa9'):
                script.get_revision(rev_id).module.upgrade()
            before = set(inspect(connection).get_table_names())
            columns_before = {t: [c["name"] for c in inspect(connection).get_columns(t)] for t in before}
            rev.module.upgrade()
            assert set(inspect(connection).get_table_names()) - before == {"risk_alert_escalations"}
            assert {t: [c["name"] for c in inspect(connection).get_columns(t)] for t in before} == columns_before
            uniques = {u["name"] for u in inspect(connection).get_unique_constraints("risk_alert_escalations")}
            assert "uq_risk_alert_escalations_alert_id" in uniques
            rev.module.downgrade()
            assert set(inspect(connection).get_table_names()) == before
    engine.dispose()


FEED_INDEXES = {'attendance_records': {'ix_attendance_records_created_at'}, 'leave_requests': {'ix_leave_requests_created_at', 'ix_leave_requests_cancelled_at', 'ix_leave_requests_reviewed_at'}, 'employees': {'ix_employees_created_at'}, 'audit_logs': {'ix_audit_logs_action_created_at'}, 'risk_alerts': {'ix_risk_alerts_resolved_at', 'ix_risk_alerts_created_at', 'ix_risk_alerts_acknowledged_at'}, 'risk_events': {'ix_risk_events_detected_at'}, 'intervention_events': {'ix_intervention_events_created_at'}, 'recommendations': {'ix_recommendations_evaluated_at', 'ix_recommendations_reviewed_at'}}


def test_feed_index_revision_only_adds_indexes() -> None:
    script = _script_directory()
    rev = script.get_revision("818f279b6dde")
    assert rev.down_revision == "0bc0f8d45e33"

    def shape(connection):
        inspector = inspect(connection)
        tables = sorted(inspector.get_table_names())
        return ({t: [(c["name"], str(c["type"]), c["nullable"]) for c in inspector.get_columns(t)] for t in tables},
                {t: {i["name"] for i in inspector.get_indexes(t)} for t in tables},
                {t: {u["name"] for u in inspector.get_unique_constraints(t)} for t in tables})

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for rev_id in ('14cb546c9a5e', '319083e42c0c', 'da1930de632b', 'a28868d7048a', '371a76ea7369', '66d767384f54', '03f5784d8edc', '70dd4dec2e60', '87cc35d2de25', 'a6382858faa9', '0bc0f8d45e33'):
                script.get_revision(rev_id).module.upgrade()
            columns_before, indexes_before, uniques_before = shape(connection)
            rev.module.upgrade()
            columns_after, indexes_after, uniques_after = shape(connection)
            assert columns_after == columns_before and uniques_after == uniques_before  # no table/column changes
            added = {t: indexes_after[t] - indexes_before[t] for t in indexes_after if indexes_after[t] - indexes_before[t]}
            assert added == FEED_INDEXES
            assert all(indexes_before[t] <= indexes_after[t] for t in indexes_before)  # nothing dropped
            assert all(len(name) <= 64 for names in added.values() for name in names)  # MySQL identifier limit
            rev.module.downgrade()
            assert shape(connection) == (columns_before, indexes_before, uniques_before)
    engine.dispose()


def test_task_revision_only_adds_task_tables() -> None:
    script = _script_directory()
    rev = script.get_revision("0d5f1e6ae076")
    assert rev.down_revision == "818f279b6dde"

    def shape(connection):
        inspector = inspect(connection)
        return {t: ([c["name"] for c in inspector.get_columns(t)], {i["name"] for i in inspector.get_indexes(t)})
                for t in inspector.get_table_names()}

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for rev_id in ('14cb546c9a5e', '319083e42c0c', 'da1930de632b', 'a28868d7048a', '371a76ea7369', '66d767384f54', '03f5784d8edc', '70dd4dec2e60', '87cc35d2de25', 'a6382858faa9', '0bc0f8d45e33', '818f279b6dde'):
                script.get_revision(rev_id).module.upgrade()
            before = shape(connection)
            rev.module.upgrade()
            after = shape(connection)
            assert set(after) - set(before) == {"tasks", "task_events"}
            assert {t: after[t] for t in before} == before  # existing tables unchanged
            inspector = inspect(connection)
            assert {i["name"] for i in inspector.get_indexes("tasks")} == {
                "ix_tasks_department_status", "ix_tasks_assignee_status", "ix_tasks_due_date",
                "ix_tasks_created_by_user_id", "ix_tasks_intervention_id", "ix_tasks_recommendation_id"}
            assert {u["name"] for u in inspector.get_unique_constraints("tasks")} == {"uq_tasks_idempotency_key"}
            assert {u["name"] for u in inspector.get_unique_constraints("task_events")} == {"uq_task_events_task_id_version"}
            checks = {c["name"] for c in inspector.get_check_constraints("tasks")}
            assert {"ck_tasks_version_positive", "ck_tasks_single_source", "ck_tasks_completed_has_time",
                    "ck_tasks_cancelled_has_reason", "ck_tasks_blocked_has_reason"} <= checks
            fks = {(f["referred_table"], f["options"].get("ondelete")) for f in inspector.get_foreign_keys("tasks")}
            assert fks == {("users", "SET NULL"), ("employees", "RESTRICT"), ("departments", "RESTRICT"),
                           ("interventions", "RESTRICT"), ("recommendations", "RESTRICT")}
            rev.module.downgrade()
            assert shape(connection) == before
    engine.dispose()


def test_performance_revision_only_adds_performance_tables() -> None:
    script = _script_directory()
    rev = script.get_revision("b647981f350c")
    assert rev.down_revision == "0d5f1e6ae076"

    def shape(connection):
        inspector = inspect(connection)
        return {t: ([c["name"] for c in inspector.get_columns(t)], {i["name"] for i in inspector.get_indexes(t)})
                for t in inspector.get_table_names()}

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for rev_id in ('14cb546c9a5e', '319083e42c0c', 'da1930de632b', 'a28868d7048a', '371a76ea7369', '66d767384f54', '03f5784d8edc', '70dd4dec2e60', '87cc35d2de25', 'a6382858faa9', '0bc0f8d45e33', '818f279b6dde', '0d5f1e6ae076'):
                script.get_revision(rev_id).module.upgrade()
            before = shape(connection)
            rev.module.upgrade()
            after = shape(connection)
            assert set(after) - set(before) == {'performance_goals', 'performance_reviews', 'performance_events', 'performance_cycles'}
            assert {t: after[t] for t in before} == before  # existing tables unchanged
            inspector = inspect(connection)
            reviews = {u["name"]: u["column_names"] for u in inspector.get_unique_constraints("performance_reviews")}
            assert reviews == {"uq_performance_reviews_cycle_id": ["cycle_id", "employee_id"]}
            assert {"ck_performance_reviews_rating_range", "ck_performance_reviews_submitted_has_rating"} <= {
                c["name"] for c in inspector.get_check_constraints("performance_reviews")}
            assert {"ck_performance_goals_progress_range", "ck_performance_goals_completed_rule"} <= {
                c["name"] for c in inspector.get_check_constraints("performance_goals")}
            assert "ck_performance_events_single_subject" in {
                c["name"] for c in inspector.get_check_constraints("performance_events")}
            assert {u["name"] for u in inspector.get_unique_constraints("performance_cycles")} == {"uq_performance_cycles_name"}
            rev.module.downgrade()
            assert shape(connection) == before
    engine.dispose()


def test_recommendation_v2_revision_is_additive_and_guarded() -> None:
    script = _script_directory()
    rev = script.get_revision("a0bd1c34f49c")
    assert rev.down_revision == "b647981f350c"
    # No longer the head since c4e7a91d3f20 (token revocation) was added on top of it.
    assert script.get_revision("c4e7a91d3f20").down_revision == "a0bd1c34f49c"
    ordered = [r.revision for r in reversed(list(script.walk_revisions()))]
    chain = ordered[:ordered.index("a0bd1c34f49c")]

    def shape(connection):
        inspector = inspect(connection)
        return {t: ([c["name"] for c in inspector.get_columns(t)], {i["name"] for i in inspector.get_indexes(t)},
                    {c["name"] for c in inspector.get_check_constraints(t)},
                    {u["name"] for u in inspector.get_unique_constraints(t)},
                    {f["name"] for f in inspector.get_foreign_keys(t)})
                for t in inspector.get_table_names()}

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for rev_id in chain:
                script.get_revision(rev_id).module.upgrade()
            before = shape(connection)
            connection.execute(text("INSERT INTO departments (id, name, is_active, created_at, updated_at) "
                                    "VALUES (1, 'D', 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
            connection.execute(text(
                "INSERT INTO recommendations (rule_id, rule_version, scope, category, priority, status, department_id, "
                "title, suggested_action, explanation, evidence, evidence_window, evaluated_at, dedup_key, version, created_at, "
                "updated_at) VALUES ('AGG_X', 'rules-v1', 'DEPARTMENT', 'DATA_QUALITY', 'LOW', 'AVAILABLE', 1, 't', 's', "
                "'e', '{}', '2026-01', '2026-01-01 00:00:00', 'k-v1', 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
            rev.module.upgrade()
            after = shape(connection)
            assert set(after) == set(before)  # no new tables
            assert {t: after[t] for t in after if t != "recommendations"} == {t: before[t] for t in before if t != "recommendations"}
            columns, indexes, checks, uniques, fks = after["recommendations"]
            old_columns, old_indexes, old_checks, old_uniques, old_fks = before["recommendations"]
            assert columns == old_columns + ["task_id", "rank_score", "priority_reasons", "evidence_fingerprint",
                                             "superseded_by_id"]
            assert indexes - old_indexes == {"ix_recommendations_task_id"} and old_indexes <= indexes
            assert checks - old_checks == {"ck_recommendations_rank_score_range"} and old_checks <= checks
            assert uniques == old_uniques
            assert fks - old_fks == {"fk_recommendations_task_id_tasks",
                                     "fk_recommendations_superseded_by_id_recommendations"}
            assert connection.execute(text("SELECT dedup_key FROM recommendations")).scalars().all() == ["k-v1"]
            connection.execute(text("UPDATE recommendations SET priority = 'CRITICAL'"))
            try:
                rev.module.downgrade()
                raise AssertionError("downgrade must refuse while rules-v2 values exist")
            except RuntimeError as exc:
                assert "rules-v2" in str(exc)
            connection.execute(text("UPDATE recommendations SET priority = 'LOW'"))
            rev.module.downgrade()
            assert shape(connection) == before
            assert connection.execute(text("SELECT dedup_key FROM recommendations")).scalars().all() == ["k-v1"]
    engine.dispose()


def test_token_revocation_revision_is_additive_and_reversible() -> None:
    """c4e7a91d3f20 adds one table (revoked_tokens) and one column (users.token_version, default 0); nothing else."""
    script = _script_directory()
    rev = script.get_revision("c4e7a91d3f20")
    assert rev.down_revision == "a0bd1c34f49c"
    ordered = [r.revision for r in reversed(list(script.walk_revisions()))]
    chain = ordered[:ordered.index("c4e7a91d3f20")]

    def shape(connection):
        inspector = inspect(connection)
        return {t: ([c["name"] for c in inspector.get_columns(t)], {i["name"] for i in inspector.get_indexes(t)},
                    {f["name"] for f in inspector.get_foreign_keys(t)})
                for t in inspector.get_table_names()}

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for rev_id in chain:
                script.get_revision(rev_id).module.upgrade()
            before = shape(connection)
            connection.execute(text("INSERT INTO users (full_name, email, password_hash, role, is_active, created_at, "
                                    "updated_at) VALUES ('U', 'u@example.com', 'x', 'ADMIN', 1, '2026-01-01 00:00:00', "
                                    "'2026-01-01 00:00:00')"))
            rev.module.upgrade()
            after = shape(connection)
            assert set(after) - set(before) == {"revoked_tokens"}
            assert after["revoked_tokens"] == (["jti", "user_id", "expires_at", "revoked_at"],
                                               {"ix_revoked_tokens_user_id", "ix_revoked_tokens_expires_at"},
                                               {"fk_revoked_tokens_user_id_users"})
            assert after["users"][0] == before["users"][0] + ["token_version"]
            assert {t: after[t] for t in before if t != "users"} == {t: before[t] for t in before if t != "users"}
            # Existing accounts start at version 0, which is what pre-upgrade tokens (no "ver" claim) carry.
            assert connection.execute(text("SELECT token_version FROM users")).scalars().all() == [0]
            rev.module.downgrade()
            assert shape(connection) == before
            assert connection.execute(text("SELECT email FROM users")).scalars().all() == ["u@example.com"]
    engine.dispose()


def _timestamp_columns() -> dict[str, list[tuple[str, bool]]]:
    from app.models.base import UTCDateTime

    return {t.name: [(c.name, c.nullable) for c in t.columns if isinstance(c.type, UTCDateTime)]
            for t in Base.metadata.sorted_tables if any(isinstance(c.type, UTCDateTime) for c in t.columns)}


def test_datetime_precision_revision_covers_every_timestamp_column() -> None:
    """5b9e2d7c41a8 (head) lists exactly the models' UTCDateTime columns with their nullability, and is a no-op on
    SQLite in both directions (SQLite already keeps microseconds)."""
    script = _script_directory()
    rev = script.get_revision("5b9e2d7c41a8")
    assert rev.down_revision == "c4e7a91d3f20"
    assert script.get_heads() == ["5b9e2d7c41a8"]
    listed = {t: sorted(cols) for t, cols in rev.module.TIMESTAMP_COLUMNS.items()}
    assert listed == {t: sorted(cols) for t, cols in _timestamp_columns().items()}
    assert sum(len(cols) for cols in listed.values()) == 90

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for revision in reversed(list(script.walk_revisions())):
                if revision.revision != "5b9e2d7c41a8":
                    revision.module.upgrade()
            before = {t: [(c["name"], str(c["type"])) for c in inspect(connection).get_columns(t)]
                      for t in inspect(connection).get_table_names()}
            rev.module.upgrade()
            rev.module.downgrade()
            assert {t: [(c["name"], str(c["type"])) for c in inspect(connection).get_columns(t)]
                    for t in inspect(connection).get_table_names()} == before
    engine.dispose()


def test_mysql_datetime_type_keeps_microseconds() -> None:
    from sqlalchemy.dialects import mysql, sqlite

    from app.models.base import UTCDateTime

    assert UTCDateTime().dialect_impl(mysql.dialect()).impl.compile(dialect=mysql.dialect()) == "DATETIME(6)"
    assert UTCDateTime().dialect_impl(sqlite.dialect()).impl.compile(dialect=sqlite.dialect()) == "DATETIME"


def test_downgrades_are_ordered_for_mysql_foreign_keys() -> None:
    """MySQL refuses to drop an index that a foreign key still needs (error 1553), and its DDL is not transactional,
    so a failing downgrade leaves the schema half-changed. Found on MySQL 8.0 in Sub-prompt 32. Rules for every
    downgrade(): no op.drop_index() for a table that the same downgrade drops (DROP TABLE removes its indexes), and in
    a batch block every foreign key is dropped before any index."""
    for path in sorted((BACKEND_DIR / "alembic" / "versions").glob("*.py")):
        body = path.read_text(encoding="utf-8").split("\ndef downgrade() -> None:\n", 1)[1]
        dropped = set(re.findall(r"op\.drop_table\('(\w+)'\)", body))
        index_tables = set(re.findall(r"op\.drop_index\([^\n]*table_name='(\w+)'\)", body))
        assert not dropped & index_tables, f"{path.name}: drop_index before drop_table for {dropped & index_tables}"
        for block in re.split(r"\n    with op\.batch_alter_table", body)[1:]:
            ops = re.findall(r"drop_index|drop_constraint\([^\n]*type_='foreignkey'", block)
            first_index = next((i for i, op_ in enumerate(ops) if op_ == "drop_index"), len(ops))
            assert all(op_ == "drop_index" for op_ in ops[first_index:]), f"{path.name}: index dropped before a foreign key"
