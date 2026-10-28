# Table inventory

Generated from the SQLAlchemy models (`app/models`, 36 tables). PK = primary key, FK = foreign key (target and ON DELETE rule).

## attendance_records

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| employee_id | INTEGER | no | FK → employees.id (RESTRICT) |
| shift_id | INTEGER | yes | FK → shifts.id (RESTRICT) |
| work_date | DATE | no |  |
| check_in | DATETIME | yes |  |
| check_out | DATETIME | yes |  |
| break_minutes | INTEGER | no |  |
| work_minutes | INTEGER | yes |  |
| overtime_minutes | INTEGER | yes |  |
| status | VARCHAR(8) | no |  |
| note | VARCHAR(500) | yes |  |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_attendance_records_break_minutes_non_negative, ck_attendance_records_checkout_after_checkin, ck_attendance_records_overtime_minutes_non_negative, ck_attendance_records_work_minutes_non_negative, uq_attendance_records_employee_id_work_date

Indexes: ix_attendance_records_created_at (created_at), ix_attendance_records_shift_id (shift_id), ix_attendance_records_status (status), ix_attendance_records_work_date (work_date)

## attrition_model_versions

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| model_version | VARCHAR(40) | no |  |
| feature_schema_version | VARCHAR(40) | no |  |
| status | VARCHAR(9) | no |  |
| trained_at | DATETIME | no |  |
| trained_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| dataset_fingerprint | VARCHAR(64) | no |  |
| artifact_sha256 | VARCHAR(64) | no |  |
| training_rows | INTEGER | no |  |
| evaluation | JSON | no |  |
| activated_at | DATETIME | yes |  |
| activated_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| dataset_version_id | INTEGER | yes | FK → dataset_versions.id (RESTRICT) |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: uq_attrition_model_versions_model_version

Indexes: ix_attrition_model_versions_dataset_version_id (dataset_version_id), ix_attrition_model_versions_status (status)

## attrition_predictions

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| employee_id | INTEGER | no | FK → employees.id (RESTRICT) |
| model_version_id | INTEGER | no | FK → attrition_model_versions.id (RESTRICT) |
| feature_schema_version | VARCHAR(40) | no |  |
| risk_score | NUMERIC(5, 2) | no |  |
| risk_category | VARCHAR(6) | no |  |
| features | JSON | no |  |
| explanation | JSON | yes |  |
| predicted_at | DATETIME | no |  |
| requested_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| batch_id | VARCHAR(36) | yes |  |

Constraints: ck_attrition_predictions_risk_score_range

Indexes: ix_attrition_predictions_batch_id (batch_id), ix_attrition_predictions_employee_predicted (employee_id, predicted_at), ix_attrition_predictions_model_version_id (model_version_id), ix_attrition_predictions_risk_category (risk_category)

## audit_logs

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| actor_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| action | VARCHAR(100) | no |  |
| entity_type | VARCHAR(100) | no |  |
| entity_id | VARCHAR(64) | yes |  |
| metadata | JSON | yes |  |
| created_at | DATETIME | no |  |

Constraints: —

Indexes: ix_audit_logs_action (action), ix_audit_logs_action_created_at (action, created_at), ix_audit_logs_actor_user_id (actor_user_id), ix_audit_logs_created_at (created_at), ix_audit_logs_entity (entity_type, entity_id)

## dataset_versions

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| dataset_id | INTEGER | no | FK → datasets.id (RESTRICT) |
| version_number | INTEGER | no |  |
| storage_key | VARCHAR(32) | no |  |
| sha256 | VARCHAR(64) | no |  |
| size_bytes | INTEGER | no |  |
| row_count | INTEGER | no |  |
| column_count | INTEGER | no |  |
| status | VARCHAR(10) | no |  |
| schema_version | VARCHAR(40) | yes |  |
| training_eligible | BOOLEAN | no | default false |
| validation_report | JSON | yes |  |
| validation_started_at | DATETIME | yes |  |
| validated_at | DATETIME | yes |  |
| uploaded_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_dataset_versions_shape_valid, ck_dataset_versions_size_positive, ck_dataset_versions_version_positive, uq_dataset_versions_dataset_id_version_number, uq_dataset_versions_sha256, uq_dataset_versions_storage_key

Indexes: ix_dataset_versions_status (status)

## datasets

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| display_name | VARCHAR(100) | no |  |
| purpose | VARCHAR(18) | no |  |
| created_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| archived_at | DATETIME | yes |  |
| archived_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: —

Indexes: —

## departments

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| name | VARCHAR(100) | no |  |
| description | TEXT | yes |  |
| is_active | BOOLEAN | no | default true |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: uq_departments_name

Indexes: —

## employees

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| employee_code | VARCHAR(20) | no |  |
| user_id | INTEGER | yes | FK → users.id (SET NULL) |
| full_name | VARCHAR(150) | no |  |
| work_email | VARCHAR(255) | no |  |
| department_id | INTEGER | no | FK → departments.id (RESTRICT) |
| designation | VARCHAR(100) | no |  |
| date_of_joining | DATE | no |  |
| status | VARCHAR(8) | no |  |
| phone | VARCHAR(20) | yes |  |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: uq_employees_employee_code, uq_employees_user_id, uq_employees_work_email

Indexes: ix_employees_created_at (created_at), ix_employees_department_id (department_id), ix_employees_status (status)

## intervention_events

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| intervention_id | INTEGER | no | FK → interventions.id (RESTRICT) |
| version | INTEGER | no |  |
| event_type | VARCHAR(10) | no |  |
| from_status | VARCHAR(11) | yes |  |
| to_status | VARCHAR(11) | yes |  |
| actor_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| note | VARCHAR(2000) | yes |  |
| details | JSON | yes |  |
| created_at | DATETIME | no |  |

Constraints: uq_intervention_events_intervention_id_version

Indexes: ix_intervention_events_created_at (created_at)

## interventions

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| employee_id | INTEGER | no | FK → employees.id (RESTRICT) |
| risk_alert_id | INTEGER | yes | FK → risk_alerts.id (RESTRICT) |
| category | VARCHAR(23) | no |  |
| title | VARCHAR(150) | no |  |
| description | VARCHAR(1000) | yes |  |
| priority | VARCHAR(6) | no |  |
| status | VARCHAR(11) | no |  |
| assigned_to_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| due_date | DATE | yes |  |
| started_at | DATETIME | yes |  |
| completed_at | DATETIME | yes |  |
| outcome | VARCHAR(1000) | yes |  |
| cancelled_at | DATETIME | yes |  |
| cancellation_reason | VARCHAR(500) | yes |  |
| created_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| updated_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| version | INTEGER | no |  |
| active_alert_key | VARCHAR(80) | yes |  |
| idempotency_key | VARCHAR(64) | yes |  |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_interventions_cancelled_has_reason, ck_interventions_completed_has_outcome, ck_interventions_inactive_has_no_key, ck_interventions_version_positive, uq_interventions_active_alert_key, uq_interventions_idempotency_key

Indexes: ix_interventions_assigned_to_user_id (assigned_to_user_id), ix_interventions_employee_status (employee_id, status), ix_interventions_priority (priority), ix_interventions_risk_alert_id (risk_alert_id), ix_interventions_status (status)

## leave_balances

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| employee_id | INTEGER | no | FK → employees.id (RESTRICT) |
| leave_type_id | INTEGER | no | FK → leave_types.id (RESTRICT) |
| year | INTEGER | no |  |
| allocated_days | INTEGER | no |  |
| carried_forward_days | INTEGER | no |  |
| used_days | INTEGER | no |  |
| pending_days | INTEGER | no |  |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_leave_balances_allocated_non_negative, ck_leave_balances_carried_non_negative, ck_leave_balances_no_overspend, ck_leave_balances_pending_non_negative, ck_leave_balances_used_non_negative, ck_leave_balances_year_range, uq_leave_balances_employee_id_leave_type_id_year

Indexes: ix_leave_balances_leave_type_id (leave_type_id)

## leave_policies

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| leave_type_id | INTEGER | no | FK → leave_types.id (RESTRICT) |
| department_id | INTEGER | yes | FK → departments.id (RESTRICT) |
| annual_allowance_days | INTEGER | no |  |
| max_consecutive_days | INTEGER | yes |  |
| carry_forward_days | INTEGER | no |  |
| count_weekends | BOOLEAN | no | default false |
| effective_from | DATE | no |  |
| effective_to | DATE | yes |  |
| is_active | BOOLEAN | no | default true |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_leave_policies_allowance_range, ck_leave_policies_carry_forward_range, ck_leave_policies_effective_range, ck_leave_policies_max_consecutive_range

Indexes: ix_leave_policies_type_department (leave_type_id, department_id)

## leave_requests

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| employee_id | INTEGER | no | FK → employees.id (RESTRICT) |
| leave_type_id | INTEGER | no | FK → leave_types.id (RESTRICT) |
| policy_id | INTEGER | no | FK → leave_policies.id (RESTRICT) |
| start_date | DATE | no |  |
| end_date | DATE | no |  |
| working_days | INTEGER | no |  |
| reason | VARCHAR(500) | no |  |
| status | VARCHAR(9) | no |  |
| reviewed_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| reviewed_at | DATETIME | yes |  |
| review_comment | VARCHAR(500) | yes |  |
| cancelled_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| cancelled_at | DATETIME | yes |  |
| cancellation_comment | VARCHAR(500) | yes |  |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_leave_requests_date_range, ck_leave_requests_working_days_range

Indexes: ix_leave_requests_cancelled_at (cancelled_at), ix_leave_requests_created_at (created_at), ix_leave_requests_employee_dates (employee_id, start_date, end_date), ix_leave_requests_leave_type_id (leave_type_id), ix_leave_requests_reviewed_at (reviewed_at), ix_leave_requests_status (status)

## leave_types

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| name | VARCHAR(100) | no |  |
| code | VARCHAR(20) | no |  |
| description | TEXT | yes |  |
| is_paid | BOOLEAN | no | default true |
| is_active | BOOLEAN | no | default true |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: uq_leave_types_code, uq_leave_types_name

Indexes: —

## notifications

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| user_id | INTEGER | no | FK → users.id (CASCADE) |
| category | VARCHAR(50) | no |  |
| title | VARCHAR(150) | no |  |
| message | VARCHAR(500) | no |  |
| entity_type | VARCHAR(50) | yes |  |
| entity_id | VARCHAR(64) | yes |  |
| is_read | BOOLEAN | no | default false |
| read_at | DATETIME | yes |  |
| created_at | DATETIME | no |  |

Constraints: —

Indexes: ix_notifications_created_at (created_at), ix_notifications_user_unread (user_id, is_read)

## payroll_line_items

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| record_id | INTEGER | no | FK → payroll_records.id (CASCADE) |
| sequence | INTEGER | no |  |
| kind | VARCHAR(9) | no |  |
| code | VARCHAR(30) | no |  |
| name | VARCHAR(100) | no |  |
| calculation_method | VARCHAR(30) | no |  |
| rate | NUMERIC(5, 2) | yes |  |
| basis | NUMERIC(12, 2) | yes |  |
| quantity | NUMERIC(7, 2) | yes |  |
| amount | NUMERIC(12, 2) | no |  |
| detail | JSON | yes |  |

Constraints: ck_payroll_line_items_amount_non_negative, ck_payroll_line_items_rate_range, uq_payroll_line_items_record_id_sequence

Indexes: —

## payroll_periods

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| year | INTEGER | no |  |
| month | INTEGER | no |  |
| start_date | DATE | no |  |
| end_date | DATE | no |  |
| created_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_payroll_periods_date_range, ck_payroll_periods_month_range, ck_payroll_periods_year_range, uq_payroll_periods_year_month

Indexes: —

## payroll_profile_components

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| profile_id | INTEGER | no | FK → payroll_profiles.id (CASCADE) |
| component_id | INTEGER | no | FK → salary_components.id (RESTRICT) |
| amount | NUMERIC(12, 2) | yes |  |
| percentage | NUMERIC(5, 2) | yes |  |

Constraints: ck_payroll_profile_components_amount_non_negative, ck_payroll_profile_components_amount_xor_percentage, ck_payroll_profile_components_percentage_range, uq_payroll_profile_components_profile_id_component_id

Indexes: ix_payroll_profile_components_component_id (component_id)

## payroll_profiles

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| employee_id | INTEGER | no | FK → employees.id (RESTRICT) |
| pay_frequency | VARCHAR(7) | no |  |
| base_salary | NUMERIC(12, 2) | no |  |
| effective_from | DATE | no |  |
| effective_to | DATE | yes |  |
| is_active | BOOLEAN | no | default true |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_payroll_profiles_base_salary_positive, ck_payroll_profiles_effective_range

Indexes: ix_payroll_profiles_employee_effective (employee_id, effective_from)

## payroll_records

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| period_id | INTEGER | no | FK → payroll_periods.id (RESTRICT) |
| employee_id | INTEGER | no | FK → employees.id (RESTRICT) |
| profile_id | INTEGER | no | FK → payroll_profiles.id (RESTRICT) |
| status | VARCHAR(10) | no |  |
| currency | VARCHAR(3) | no |  |
| base_salary | NUMERIC(12, 2) | no |  |
| gross_earnings | NUMERIC(12, 2) | no |  |
| total_deductions | NUMERIC(12, 2) | no |  |
| net_pay | NUMERIC(12, 2) | no |  |
| warnings | JSON | yes |  |
| calculated_at | DATETIME | no |  |
| calculated_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| approved_at | DATETIME | yes |  |
| approved_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| paid_at | DATETIME | yes |  |
| paid_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| payment_reference | VARCHAR(100) | yes |  |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_payroll_records_base_salary_positive, ck_payroll_records_deductions_non_negative, ck_payroll_records_gross_non_negative, ck_payroll_records_net_is_gross_minus_deductions, ck_payroll_records_net_non_negative, uq_payroll_records_period_id_employee_id

Indexes: ix_payroll_records_employee_period (employee_id, period_id), ix_payroll_records_status (status)

## performance_cycles

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| name | VARCHAR(120) | no |  |
| description | VARCHAR(2000) | yes |  |
| period_start | DATE | no |  |
| period_end | DATE | no |  |
| self_assessment_deadline | DATE | no |  |
| manager_review_deadline | DATE | no |  |
| status | VARCHAR(11) | no |  |
| cancellation_reason | VARCHAR(500) | yes |  |
| created_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| updated_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| version | INTEGER | no |  |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_performance_cycles_cancelled_has_reason, ck_performance_cycles_deadlines_ordered, ck_performance_cycles_period_ordered, ck_performance_cycles_version_positive, uq_performance_cycles_name

Indexes: ix_performance_cycles_status (status)

## performance_events

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| cycle_id | INTEGER | yes | FK → performance_cycles.id (RESTRICT) |
| goal_id | INTEGER | yes | FK → performance_goals.id (RESTRICT) |
| review_id | INTEGER | yes | FK → performance_reviews.id (RESTRICT) |
| version | INTEGER | no |  |
| event_type | VARCHAR(25) | no |  |
| from_status | VARCHAR(30) | yes |  |
| to_status | VARCHAR(30) | yes |  |
| actor_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| details | JSON | yes |  |
| created_at | DATETIME | no |  |

Constraints: ck_performance_events_single_subject, uq_performance_events_cycle_id, uq_performance_events_goal_id, uq_performance_events_review_id

Indexes: —

## performance_goals

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| employee_id | INTEGER | no | FK → employees.id (RESTRICT) |
| cycle_id | INTEGER | yes | FK → performance_cycles.id (RESTRICT) |
| title | VARCHAR(150) | no |  |
| description | VARCHAR(2000) | yes |  |
| category | VARCHAR(11) | no |  |
| measurable_target | VARCHAR(255) | no |  |
| target_value | NUMERIC(12, 2) | yes |  |
| unit | VARCHAR(30) | yes |  |
| start_date | DATE | no |  |
| target_date | DATE | no |  |
| progress_percent | SMALLINT | no |  |
| status | VARCHAR(11) | no |  |
| completed_at | DATETIME | yes |  |
| manager_feedback | VARCHAR(2000) | yes |  |
| created_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| updated_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| version | INTEGER | no |  |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_performance_goals_completed_rule, ck_performance_goals_dates_ordered, ck_performance_goals_progress_range, ck_performance_goals_version_positive

Indexes: ix_performance_goals_cycle_id (cycle_id), ix_performance_goals_employee_status (employee_id, status)

## performance_reviews

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| cycle_id | INTEGER | no | FK → performance_cycles.id (RESTRICT) |
| employee_id | INTEGER | no | FK → employees.id (RESTRICT) |
| reviewer_user_id | INTEGER | no | FK → users.id (RESTRICT) |
| status | VARCHAR(17) | no |  |
| self_assessment | TEXT | yes |  |
| self_submitted_at | DATETIME | yes |  |
| manager_summary | TEXT | yes |  |
| strengths | JSON | yes |  |
| improvement_areas | JSON | yes |  |
| development_goals | JSON | yes |  |
| training_recommendations | JSON | yes |  |
| manager_private_notes | VARCHAR(2000) | yes |  |
| overall_rating | SMALLINT | yes |  |
| manager_submitted_at | DATETIME | yes |  |
| finalized_at | DATETIME | yes |  |
| finalized_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| cancelled_at | DATETIME | yes |  |
| cancellation_reason | VARCHAR(500) | yes |  |
| reopen_reason | VARCHAR(500) | yes |  |
| created_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| updated_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| version | INTEGER | no |  |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_performance_reviews_cancelled_has_reason, ck_performance_reviews_finalized_has_time, ck_performance_reviews_rating_range, ck_performance_reviews_submitted_has_rating, ck_performance_reviews_version_positive, uq_performance_reviews_cycle_id

Indexes: ix_performance_reviews_employee_status (employee_id, status), ix_performance_reviews_reviewer_user_id (reviewer_user_id), ix_performance_reviews_status (status)

## recommendations

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| rule_id | VARCHAR(50) | no |  |
| rule_version | VARCHAR(20) | no |  |
| scope | VARCHAR(12) | no |  |
| category | VARCHAR(27) | no |  |
| priority | VARCHAR(8) | no |  |
| status | VARCHAR(10) | no |  |
| employee_id | INTEGER | yes | FK → employees.id (RESTRICT) |
| department_id | INTEGER | yes | FK → departments.id (RESTRICT) |
| risk_alert_id | INTEGER | yes | FK → risk_alerts.id (RESTRICT) |
| intervention_id | INTEGER | yes | FK → interventions.id (RESTRICT) |
| title | VARCHAR(150) | no |  |
| suggested_action | VARCHAR(500) | no |  |
| explanation | VARCHAR(1000) | no |  |
| evidence | JSON | no |  |
| evidence_window | VARCHAR(60) | no |  |
| source_data_as_of | DATETIME | yes |  |
| evaluated_at | DATETIME | no |  |
| dedup_key | VARCHAR(200) | no |  |
| reviewed_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| reviewed_at | DATETIME | yes |  |
| dismissal_reason | VARCHAR(500) | yes |  |
| completion_outcome | VARCHAR(1000) | yes |  |
| version | INTEGER | no |  |
| task_id | INTEGER | yes | FK → tasks.id (RESTRICT) |
| rank_score | SMALLINT | yes |  |
| priority_reasons | JSON | yes |  |
| evidence_fingerprint | VARCHAR(64) | yes |  |
| superseded_by_id | INTEGER | yes | FK → recommendations.id (RESTRICT) |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_recommendations_completed_has_outcome, ck_recommendations_dismissed_has_reason, ck_recommendations_rank_score_range, ck_recommendations_scope_subject, ck_recommendations_version_positive, uq_recommendations_dedup_key

Indexes: ix_recommendations_department_id (department_id), ix_recommendations_employee_id (employee_id), ix_recommendations_evaluated_at (evaluated_at), ix_recommendations_reviewed_at (reviewed_at), ix_recommendations_risk_alert_id (risk_alert_id), ix_recommendations_rule_id (rule_id), ix_recommendations_scope_status (scope, status), ix_recommendations_status (status), ix_recommendations_task_id (task_id)

## revoked_tokens

| Column | Type | Null | Key / default |
|---|---|---|---|
| jti | VARCHAR(64) | no | PK |
| user_id | INTEGER | no | FK → users.id (CASCADE) |
| expires_at | DATETIME | no |  |
| revoked_at | DATETIME | no |  |

Constraints: —

Indexes: ix_revoked_tokens_expires_at (expires_at), ix_revoked_tokens_user_id (user_id)

## risk_alert_escalations

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| alert_id | INTEGER | no | FK → risk_alerts.id (RESTRICT) |
| stage | VARCHAR(11) | no |  |
| alert_priority | VARCHAR(6) | no |  |
| review_due_at | DATETIME | no |  |
| recipient_count | INTEGER | no |  |
| triggered_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| created_at | DATETIME | no |  |

Constraints: ck_risk_alert_escalations_recipient_count_positive, uq_risk_alert_escalations_alert_id

Indexes: ix_risk_alert_escalations_created_at (created_at)

## risk_alerts

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| employee_id | INTEGER | no | FK → employees.id (RESTRICT) |
| trigger_type | VARCHAR(17) | no |  |
| priority | VARCHAR(6) | no |  |
| status | VARCHAR(12) | no |  |
| latest_prediction_id | INTEGER | no | FK → attrition_predictions.id (RESTRICT) |
| latest_score | NUMERIC(5, 2) | no |  |
| latest_category | VARCHAR(6) | no |  |
| model_version | VARCHAR(40) | no |  |
| event_count | INTEGER | no |  |
| first_detected_at | DATETIME | no |  |
| last_observed_at | DATETIME | no |  |
| assigned_to_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| acknowledged_at | DATETIME | yes |  |
| acknowledged_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| resolved_at | DATETIME | yes |  |
| resolved_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| resolution_reason | VARCHAR(500) | yes |  |
| reviewer_notes | VARCHAR(1000) | yes |  |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_risk_alerts_event_count_positive, ck_risk_alerts_latest_score_range

Indexes: ix_risk_alerts_acknowledged_at (acknowledged_at), ix_risk_alerts_assigned_to_user_id (assigned_to_user_id), ix_risk_alerts_created_at (created_at), ix_risk_alerts_employee_status (employee_id, status), ix_risk_alerts_priority (priority), ix_risk_alerts_resolved_at (resolved_at), ix_risk_alerts_status (status)

## risk_events

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| employee_id | INTEGER | no | FK → employees.id (RESTRICT) |
| event_type | VARCHAR(17) | no |  |
| dedup_key | VARCHAR(100) | no |  |
| current_prediction_id | INTEGER | no | FK → attrition_predictions.id (RESTRICT) |
| previous_prediction_id | INTEGER | yes | FK → attrition_predictions.id (RESTRICT) |
| current_score | NUMERIC(5, 2) | no |  |
| previous_score | NUMERIC(5, 2) | yes |  |
| current_category | VARCHAR(6) | no |  |
| previous_category | VARCHAR(6) | yes |  |
| model_version | VARCHAR(40) | no |  |
| detected_at | DATETIME | no |  |
| alert_id | INTEGER | yes | FK → risk_alerts.id (RESTRICT) |

Constraints: ck_risk_events_current_score_range, ck_risk_events_previous_score_range, uq_risk_events_dedup_key

Indexes: ix_risk_events_alert_id (alert_id), ix_risk_events_detected_at (detected_at), ix_risk_events_employee_detected (employee_id, detected_at), ix_risk_events_event_type (event_type)

## salary_components

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| code | VARCHAR(30) | no |  |
| name | VARCHAR(100) | no |  |
| description | TEXT | yes |  |
| kind | VARCHAR(9) | no |  |
| calculation_method | VARCHAR(16) | no |  |
| display_order | INTEGER | no |  |
| is_active | BOOLEAN | no | default true |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_salary_components_earning_not_percent_of_gross, uq_salary_components_code, uq_salary_components_name

Indexes: —

## shifts

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| name | VARCHAR(100) | no |  |
| start_time | TIME | no |  |
| end_time | TIME | no |  |
| description | TEXT | yes |  |
| break_minutes | INTEGER | no |  |
| overtime_threshold_minutes | INTEGER | no |  |
| is_active | BOOLEAN | no | default true |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_shifts_break_minutes_range, ck_shifts_distinct_times, ck_shifts_overtime_threshold_range, uq_shifts_name

Indexes: —

## task_events

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| task_id | INTEGER | no | FK → tasks.id (RESTRICT) |
| version | INTEGER | no |  |
| event_type | VARCHAR(9) | no |  |
| from_status | VARCHAR(11) | yes |  |
| to_status | VARCHAR(11) | yes |  |
| actor_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| details | JSON | yes |  |
| created_at | DATETIME | no |  |

Constraints: uq_task_events_task_id_version

Indexes: —

## tasks

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| title | VARCHAR(150) | no |  |
| description | VARCHAR(2000) | yes |  |
| task_type | VARCHAR(13) | no |  |
| priority | VARCHAR(8) | no |  |
| status | VARCHAR(11) | no |  |
| created_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| updated_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| assignee_employee_id | INTEGER | yes | FK → employees.id (RESTRICT) |
| department_id | INTEGER | yes | FK → departments.id (RESTRICT) |
| due_date | DATE | yes |  |
| started_at | DATETIME | yes |  |
| completed_at | DATETIME | yes |  |
| completion_summary | VARCHAR(2000) | yes |  |
| cancelled_at | DATETIME | yes |  |
| cancellation_reason | VARCHAR(500) | yes |  |
| block_reason | VARCHAR(22) | yes |  |
| block_details | VARCHAR(500) | yes |  |
| intervention_id | INTEGER | yes | FK → interventions.id (RESTRICT) |
| recommendation_id | INTEGER | yes | FK → recommendations.id (RESTRICT) |
| version | INTEGER | no |  |
| idempotency_key | VARCHAR(64) | yes |  |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: ck_tasks_blocked_has_reason, ck_tasks_cancelled_has_reason, ck_tasks_completed_has_time, ck_tasks_single_source, ck_tasks_version_positive, uq_tasks_idempotency_key

Indexes: ix_tasks_assignee_status (assignee_employee_id, status), ix_tasks_created_by_user_id (created_by_user_id), ix_tasks_department_status (department_id, status), ix_tasks_due_date (due_date), ix_tasks_intervention_id (intervention_id), ix_tasks_recommendation_id (recommendation_id)

## users

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| full_name | VARCHAR(150) | no |  |
| email | VARCHAR(255) | no |  |
| password_hash | VARCHAR(255) | no |  |
| role | VARCHAR(8) | no |  |
| is_active | BOOLEAN | no | default true |
| last_login_at | DATETIME | yes |  |
| token_version | INTEGER | no | default 0 |
| created_at | DATETIME | no |  |
| updated_at | DATETIME | no |  |

Constraints: uq_users_email

Indexes: ix_users_role (role)

## workforce_snapshot_departments

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| snapshot_id | INTEGER | no | FK → workforce_snapshots.id (RESTRICT) |
| department_id | INTEGER | no | FK → departments.id (RESTRICT) |
| department_name | VARCHAR(100) | no |  |
| total_employees | INTEGER | no |  |
| active_employees | INTEGER | no |  |
| on_leave_employees | INTEGER | no |  |
| inactive_employees | INTEGER | no |  |
| joiners_in_period | INTEGER | no |  |
| recorded_departures_in_period | INTEGER | no |  |

Constraints: ck_workforce_snapshot_departments_counts_valid, uq_workforce_snapshot_departments_snapshot_id_department_id

Indexes: ix_workforce_snapshot_departments_department_id (department_id)

## workforce_snapshots

| Column | Type | Null | Key / default |
|---|---|---|---|
| id | INTEGER | no | PK |
| snapshot_date | DATE | no |  |
| captured_at | DATETIME | no |  |
| captured_by_user_id | INTEGER | yes | FK → users.id (SET NULL) |
| period_start | DATE | no |  |
| total_employees | INTEGER | no |  |
| active_employees | INTEGER | no |  |
| on_leave_employees | INTEGER | no |  |
| inactive_employees | INTEGER | no |  |
| joiners_in_period | INTEGER | no |  |
| recorded_departures_in_period | INTEGER | no |  |
| source | VARCHAR(50) | no |  |
| data_quality | JSON | no |  |

Constraints: ck_workforce_snapshots_counts_valid, ck_workforce_snapshots_period_valid, uq_workforce_snapshots_snapshot_date

Indexes: —

