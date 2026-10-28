# Entity relationship diagram

Generated from the SQLAlchemy models (foreign keys). Render with any Mermaid viewer (GitHub and VS Code preview render it).

```mermaid
erDiagram
    attendance_records {
        INTEGER id PK
        INTEGER employee_id FK
        INTEGER shift_id FK
        DATE work_date
        DATETIME check_in
        DATETIME check_out
        INTEGER break_minutes
        INTEGER work_minutes
        INTEGER overtime_minutes
        VARCHAR status
        VARCHAR note
        DATETIME created_at
        DATETIME updated_at
    }
    attrition_model_versions {
        INTEGER id PK
        VARCHAR model_version
        VARCHAR feature_schema_version
        VARCHAR status
        DATETIME trained_at
        INTEGER trained_by_user_id FK
        VARCHAR dataset_fingerprint
        VARCHAR artifact_sha256
        INTEGER training_rows
        JSON evaluation
        DATETIME activated_at
        INTEGER activated_by_user_id FK
        INTEGER dataset_version_id FK
        DATETIME created_at
        DATETIME updated_at
    }
    attrition_predictions {
        INTEGER id PK
        INTEGER employee_id FK
        INTEGER model_version_id FK
        VARCHAR feature_schema_version
        NUMERIC risk_score
        VARCHAR risk_category
        JSON features
        JSON explanation
        DATETIME predicted_at
        INTEGER requested_by_user_id FK
        VARCHAR batch_id
    }
    audit_logs {
        INTEGER id PK
        INTEGER actor_user_id FK
        VARCHAR action
        VARCHAR entity_type
        VARCHAR entity_id
        JSON metadata
        DATETIME created_at
    }
    dataset_versions {
        INTEGER id PK
        INTEGER dataset_id FK
        INTEGER version_number
        VARCHAR storage_key
        VARCHAR sha256
        INTEGER size_bytes
        INTEGER row_count
        INTEGER column_count
        VARCHAR status
        VARCHAR schema_version
        BOOLEAN training_eligible
        JSON validation_report
        DATETIME validation_started_at
        DATETIME validated_at
        INTEGER uploaded_by_user_id FK
        DATETIME created_at
        DATETIME updated_at
    }
    datasets {
        INTEGER id PK
        VARCHAR display_name
        VARCHAR purpose
        INTEGER created_by_user_id FK
        DATETIME archived_at
        INTEGER archived_by_user_id FK
        DATETIME created_at
        DATETIME updated_at
    }
    departments {
        INTEGER id PK
        VARCHAR name
        TEXT description
        BOOLEAN is_active
        DATETIME created_at
        DATETIME updated_at
    }
    employees {
        INTEGER id PK
        VARCHAR employee_code
        INTEGER user_id FK
        VARCHAR full_name
        VARCHAR work_email
        INTEGER department_id FK
        VARCHAR designation
        DATE date_of_joining
        VARCHAR status
        VARCHAR phone
        DATETIME created_at
        DATETIME updated_at
    }
    intervention_events {
        INTEGER id PK
        INTEGER intervention_id FK
        INTEGER version
        VARCHAR event_type
        VARCHAR from_status
        VARCHAR to_status
        INTEGER actor_user_id FK
        VARCHAR note
        JSON details
        DATETIME created_at
    }
    interventions {
        INTEGER id PK
        INTEGER employee_id FK
        INTEGER risk_alert_id FK
        VARCHAR category
        VARCHAR title
        VARCHAR description
        VARCHAR priority
        VARCHAR status
        INTEGER assigned_to_user_id FK
        DATE due_date
        DATETIME started_at
        DATETIME completed_at
        VARCHAR outcome
        DATETIME cancelled_at
        VARCHAR cancellation_reason
        INTEGER created_by_user_id FK
        INTEGER updated_by_user_id FK
        INTEGER version
        VARCHAR active_alert_key
        VARCHAR idempotency_key
        DATETIME created_at
        DATETIME updated_at
    }
    leave_balances {
        INTEGER id PK
        INTEGER employee_id FK
        INTEGER leave_type_id FK
        INTEGER year
        INTEGER allocated_days
        INTEGER carried_forward_days
        INTEGER used_days
        INTEGER pending_days
        DATETIME created_at
        DATETIME updated_at
    }
    leave_policies {
        INTEGER id PK
        INTEGER leave_type_id FK
        INTEGER department_id FK
        INTEGER annual_allowance_days
        INTEGER max_consecutive_days
        INTEGER carry_forward_days
        BOOLEAN count_weekends
        DATE effective_from
        DATE effective_to
        BOOLEAN is_active
        DATETIME created_at
        DATETIME updated_at
    }
    leave_requests {
        INTEGER id PK
        INTEGER employee_id FK
        INTEGER leave_type_id FK
        INTEGER policy_id FK
        DATE start_date
        DATE end_date
        INTEGER working_days
        VARCHAR reason
        VARCHAR status
        INTEGER reviewed_by_user_id FK
        DATETIME reviewed_at
        VARCHAR review_comment
        INTEGER cancelled_by_user_id FK
        DATETIME cancelled_at
        VARCHAR cancellation_comment
        DATETIME created_at
        DATETIME updated_at
    }
    leave_types {
        INTEGER id PK
        VARCHAR name
        VARCHAR code
        TEXT description
        BOOLEAN is_paid
        BOOLEAN is_active
        DATETIME created_at
        DATETIME updated_at
    }
    notifications {
        INTEGER id PK
        INTEGER user_id FK
        VARCHAR category
        VARCHAR title
        VARCHAR message
        VARCHAR entity_type
        VARCHAR entity_id
        BOOLEAN is_read
        DATETIME read_at
        DATETIME created_at
    }
    payroll_line_items {
        INTEGER id PK
        INTEGER record_id FK
        INTEGER sequence
        VARCHAR kind
        VARCHAR code
        VARCHAR name
        VARCHAR calculation_method
        NUMERIC rate
        NUMERIC basis
        NUMERIC quantity
        NUMERIC amount
        JSON detail
    }
    payroll_periods {
        INTEGER id PK
        INTEGER year
        INTEGER month
        DATE start_date
        DATE end_date
        INTEGER created_by_user_id FK
        DATETIME created_at
        DATETIME updated_at
    }
    payroll_profile_components {
        INTEGER id PK
        INTEGER profile_id FK
        INTEGER component_id FK
        NUMERIC amount
        NUMERIC percentage
    }
    payroll_profiles {
        INTEGER id PK
        INTEGER employee_id FK
        VARCHAR pay_frequency
        NUMERIC base_salary
        DATE effective_from
        DATE effective_to
        BOOLEAN is_active
        DATETIME created_at
        DATETIME updated_at
    }
    payroll_records {
        INTEGER id PK
        INTEGER period_id FK
        INTEGER employee_id FK
        INTEGER profile_id FK
        VARCHAR status
        VARCHAR currency
        NUMERIC base_salary
        NUMERIC gross_earnings
        NUMERIC total_deductions
        NUMERIC net_pay
        JSON warnings
        DATETIME calculated_at
        INTEGER calculated_by_user_id FK
        DATETIME approved_at
        INTEGER approved_by_user_id FK
        DATETIME paid_at
        INTEGER paid_by_user_id FK
        VARCHAR payment_reference
        DATETIME created_at
        DATETIME updated_at
    }
    performance_cycles {
        INTEGER id PK
        VARCHAR name
        VARCHAR description
        DATE period_start
        DATE period_end
        DATE self_assessment_deadline
        DATE manager_review_deadline
        VARCHAR status
        VARCHAR cancellation_reason
        INTEGER created_by_user_id FK
        INTEGER updated_by_user_id FK
        INTEGER version
        DATETIME created_at
        DATETIME updated_at
    }
    performance_events {
        INTEGER id PK
        INTEGER cycle_id FK
        INTEGER goal_id FK
        INTEGER review_id FK
        INTEGER version
        VARCHAR event_type
        VARCHAR from_status
        VARCHAR to_status
        INTEGER actor_user_id FK
        JSON details
        DATETIME created_at
    }
    performance_goals {
        INTEGER id PK
        INTEGER employee_id FK
        INTEGER cycle_id FK
        VARCHAR title
        VARCHAR description
        VARCHAR category
        VARCHAR measurable_target
        NUMERIC target_value
        VARCHAR unit
        DATE start_date
        DATE target_date
        SMALLINT progress_percent
        VARCHAR status
        DATETIME completed_at
        VARCHAR manager_feedback
        INTEGER created_by_user_id FK
        INTEGER updated_by_user_id FK
        INTEGER version
        DATETIME created_at
        DATETIME updated_at
    }
    performance_reviews {
        INTEGER id PK
        INTEGER cycle_id FK
        INTEGER employee_id FK
        INTEGER reviewer_user_id FK
        VARCHAR status
        TEXT self_assessment
        DATETIME self_submitted_at
        TEXT manager_summary
        JSON strengths
        JSON improvement_areas
        JSON development_goals
        JSON training_recommendations
        VARCHAR manager_private_notes
        SMALLINT overall_rating
        DATETIME manager_submitted_at
        DATETIME finalized_at
        INTEGER finalized_by_user_id FK
        DATETIME cancelled_at
        VARCHAR cancellation_reason
        VARCHAR reopen_reason
        INTEGER created_by_user_id FK
        INTEGER updated_by_user_id FK
        INTEGER version
        DATETIME created_at
        DATETIME updated_at
    }
    recommendations {
        INTEGER id PK
        VARCHAR rule_id
        VARCHAR rule_version
        VARCHAR scope
        VARCHAR category
        VARCHAR priority
        VARCHAR status
        INTEGER employee_id FK
        INTEGER department_id FK
        INTEGER risk_alert_id FK
        INTEGER intervention_id FK
        VARCHAR title
        VARCHAR suggested_action
        VARCHAR explanation
        JSON evidence
        VARCHAR evidence_window
        DATETIME source_data_as_of
        DATETIME evaluated_at
        VARCHAR dedup_key
        INTEGER reviewed_by_user_id FK
        DATETIME reviewed_at
        VARCHAR dismissal_reason
        VARCHAR completion_outcome
        INTEGER version
        INTEGER task_id FK
        SMALLINT rank_score
        JSON priority_reasons
        VARCHAR evidence_fingerprint
        INTEGER superseded_by_id FK
        DATETIME created_at
        DATETIME updated_at
    }
    revoked_tokens {
        VARCHAR jti PK
        INTEGER user_id FK
        DATETIME expires_at
        DATETIME revoked_at
    }
    risk_alert_escalations {
        INTEGER id PK
        INTEGER alert_id FK
        VARCHAR stage
        VARCHAR alert_priority
        DATETIME review_due_at
        INTEGER recipient_count
        INTEGER triggered_by_user_id FK
        DATETIME created_at
    }
    risk_alerts {
        INTEGER id PK
        INTEGER employee_id FK
        VARCHAR trigger_type
        VARCHAR priority
        VARCHAR status
        INTEGER latest_prediction_id FK
        NUMERIC latest_score
        VARCHAR latest_category
        VARCHAR model_version
        INTEGER event_count
        DATETIME first_detected_at
        DATETIME last_observed_at
        INTEGER assigned_to_user_id FK
        DATETIME acknowledged_at
        INTEGER acknowledged_by_user_id FK
        DATETIME resolved_at
        INTEGER resolved_by_user_id FK
        VARCHAR resolution_reason
        VARCHAR reviewer_notes
        DATETIME created_at
        DATETIME updated_at
    }
    risk_events {
        INTEGER id PK
        INTEGER employee_id FK
        VARCHAR event_type
        VARCHAR dedup_key
        INTEGER current_prediction_id FK
        INTEGER previous_prediction_id FK
        NUMERIC current_score
        NUMERIC previous_score
        VARCHAR current_category
        VARCHAR previous_category
        VARCHAR model_version
        DATETIME detected_at
        INTEGER alert_id FK
    }
    salary_components {
        INTEGER id PK
        VARCHAR code
        VARCHAR name
        TEXT description
        VARCHAR kind
        VARCHAR calculation_method
        INTEGER display_order
        BOOLEAN is_active
        DATETIME created_at
        DATETIME updated_at
    }
    shifts {
        INTEGER id PK
        VARCHAR name
        TIME start_time
        TIME end_time
        TEXT description
        INTEGER break_minutes
        INTEGER overtime_threshold_minutes
        BOOLEAN is_active
        DATETIME created_at
        DATETIME updated_at
    }
    task_events {
        INTEGER id PK
        INTEGER task_id FK
        INTEGER version
        VARCHAR event_type
        VARCHAR from_status
        VARCHAR to_status
        INTEGER actor_user_id FK
        JSON details
        DATETIME created_at
    }
    tasks {
        INTEGER id PK
        VARCHAR title
        VARCHAR description
        VARCHAR task_type
        VARCHAR priority
        VARCHAR status
        INTEGER created_by_user_id FK
        INTEGER updated_by_user_id FK
        INTEGER assignee_employee_id FK
        INTEGER department_id FK
        DATE due_date
        DATETIME started_at
        DATETIME completed_at
        VARCHAR completion_summary
        DATETIME cancelled_at
        VARCHAR cancellation_reason
        VARCHAR block_reason
        VARCHAR block_details
        INTEGER intervention_id FK
        INTEGER recommendation_id FK
        INTEGER version
        VARCHAR idempotency_key
        DATETIME created_at
        DATETIME updated_at
    }
    users {
        INTEGER id PK
        VARCHAR full_name
        VARCHAR email
        VARCHAR password_hash
        VARCHAR role
        BOOLEAN is_active
        DATETIME last_login_at
        INTEGER token_version
        DATETIME created_at
        DATETIME updated_at
    }
    workforce_snapshot_departments {
        INTEGER id PK
        INTEGER snapshot_id FK
        INTEGER department_id FK
        VARCHAR department_name
        INTEGER total_employees
        INTEGER active_employees
        INTEGER on_leave_employees
        INTEGER inactive_employees
        INTEGER joiners_in_period
        INTEGER recorded_departures_in_period
    }
    workforce_snapshots {
        INTEGER id PK
        DATE snapshot_date
        DATETIME captured_at
        INTEGER captured_by_user_id FK
        DATE period_start
        INTEGER total_employees
        INTEGER active_employees
        INTEGER on_leave_employees
        INTEGER inactive_employees
        INTEGER joiners_in_period
        INTEGER recorded_departures_in_period
        VARCHAR source
        JSON data_quality
    }
    shifts ||--o{ attendance_records : "shift_id (optional)"
    employees ||--o{ attendance_records : "employee_id"
    users ||--o{ attrition_model_versions : "activated_by_user_id (optional)"
    users ||--o{ attrition_model_versions : "trained_by_user_id (optional)"
    dataset_versions ||--o{ attrition_model_versions : "dataset_version_id (optional)"
    attrition_model_versions ||--o{ attrition_predictions : "model_version_id"
    employees ||--o{ attrition_predictions : "employee_id"
    users ||--o{ attrition_predictions : "requested_by_user_id (optional)"
    users ||--o{ audit_logs : "actor_user_id (optional)"
    users ||--o{ dataset_versions : "uploaded_by_user_id (optional)"
    datasets ||--o{ dataset_versions : "dataset_id"
    users ||--o{ datasets : "archived_by_user_id (optional)"
    users ||--o{ datasets : "created_by_user_id (optional)"
    users ||--o{ employees : "user_id (optional)"
    departments ||--o{ employees : "department_id"
    interventions ||--o{ intervention_events : "intervention_id"
    users ||--o{ intervention_events : "actor_user_id (optional)"
    users ||--o{ interventions : "assigned_to_user_id (optional)"
    risk_alerts ||--o{ interventions : "risk_alert_id (optional)"
    users ||--o{ interventions : "updated_by_user_id (optional)"
    users ||--o{ interventions : "created_by_user_id (optional)"
    employees ||--o{ interventions : "employee_id"
    employees ||--o{ leave_balances : "employee_id"
    leave_types ||--o{ leave_balances : "leave_type_id"
    leave_types ||--o{ leave_policies : "leave_type_id"
    departments ||--o{ leave_policies : "department_id (optional)"
    leave_policies ||--o{ leave_requests : "policy_id"
    users ||--o{ leave_requests : "cancelled_by_user_id (optional)"
    users ||--o{ leave_requests : "reviewed_by_user_id (optional)"
    leave_types ||--o{ leave_requests : "leave_type_id"
    employees ||--o{ leave_requests : "employee_id"
    users ||--o{ notifications : "user_id"
    payroll_records ||--o{ payroll_line_items : "record_id"
    users ||--o{ payroll_periods : "created_by_user_id (optional)"
    payroll_profiles ||--o{ payroll_profile_components : "profile_id"
    salary_components ||--o{ payroll_profile_components : "component_id"
    employees ||--o{ payroll_profiles : "employee_id"
    users ||--o{ payroll_records : "calculated_by_user_id (optional)"
    users ||--o{ payroll_records : "paid_by_user_id (optional)"
    employees ||--o{ payroll_records : "employee_id"
    users ||--o{ payroll_records : "approved_by_user_id (optional)"
    payroll_profiles ||--o{ payroll_records : "profile_id"
    payroll_periods ||--o{ payroll_records : "period_id"
    users ||--o{ performance_cycles : "created_by_user_id (optional)"
    users ||--o{ performance_cycles : "updated_by_user_id (optional)"
    users ||--o{ performance_events : "actor_user_id (optional)"
    performance_reviews ||--o{ performance_events : "review_id (optional)"
    performance_cycles ||--o{ performance_events : "cycle_id (optional)"
    performance_goals ||--o{ performance_events : "goal_id (optional)"
    performance_cycles ||--o{ performance_goals : "cycle_id (optional)"
    users ||--o{ performance_goals : "created_by_user_id (optional)"
    users ||--o{ performance_goals : "updated_by_user_id (optional)"
    employees ||--o{ performance_goals : "employee_id"
    users ||--o{ performance_reviews : "reviewer_user_id"
    performance_cycles ||--o{ performance_reviews : "cycle_id"
    users ||--o{ performance_reviews : "finalized_by_user_id (optional)"
    users ||--o{ performance_reviews : "updated_by_user_id (optional)"
    users ||--o{ performance_reviews : "created_by_user_id (optional)"
    employees ||--o{ performance_reviews : "employee_id"
    users ||--o{ recommendations : "reviewed_by_user_id (optional)"
    interventions ||--o{ recommendations : "intervention_id (optional)"
    risk_alerts ||--o{ recommendations : "risk_alert_id (optional)"
    recommendations ||--o{ recommendations : "superseded_by_id (optional)"
    tasks ||--o{ recommendations : "task_id (optional)"
    employees ||--o{ recommendations : "employee_id (optional)"
    departments ||--o{ recommendations : "department_id (optional)"
    users ||--o{ revoked_tokens : "user_id"
    risk_alerts ||--o{ risk_alert_escalations : "alert_id"
    users ||--o{ risk_alert_escalations : "triggered_by_user_id (optional)"
    users ||--o{ risk_alerts : "assigned_to_user_id (optional)"
    attrition_predictions ||--o{ risk_alerts : "latest_prediction_id"
    users ||--o{ risk_alerts : "resolved_by_user_id (optional)"
    users ||--o{ risk_alerts : "acknowledged_by_user_id (optional)"
    employees ||--o{ risk_alerts : "employee_id"
    employees ||--o{ risk_events : "employee_id"
    attrition_predictions ||--o{ risk_events : "current_prediction_id"
    attrition_predictions ||--o{ risk_events : "previous_prediction_id (optional)"
    risk_alerts ||--o{ risk_events : "alert_id (optional)"
    users ||--o{ task_events : "actor_user_id (optional)"
    tasks ||--o{ task_events : "task_id"
    users ||--o{ tasks : "created_by_user_id (optional)"
    interventions ||--o{ tasks : "intervention_id (optional)"
    departments ||--o{ tasks : "department_id (optional)"
    employees ||--o{ tasks : "assignee_employee_id (optional)"
    users ||--o{ tasks : "updated_by_user_id (optional)"
    recommendations ||--o{ tasks : "recommendation_id (optional)"
    workforce_snapshots ||--o{ workforce_snapshot_departments : "snapshot_id"
    departments ||--o{ workforce_snapshot_departments : "department_id"
    users ||--o{ workforce_snapshots : "captured_by_user_id (optional)"
```
