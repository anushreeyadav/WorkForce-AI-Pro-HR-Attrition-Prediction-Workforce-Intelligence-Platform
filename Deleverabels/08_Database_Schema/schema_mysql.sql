-- WorkForce AI Pro: MySQL DDL rendered OFFLINE by `alembic upgrade head --sql` (nothing was executed;
-- no database was contacted). Schema only: no data, users or credentials.
CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 14cb546c9a5e

CREATE TABLE departments (
    id INTEGER NOT NULL AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    is_active BOOL NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_departments PRIMARY KEY (id),
    CONSTRAINT uq_departments_name UNIQUE (name)
);

CREATE TABLE users (
    id INTEGER NOT NULL AUTO_INCREMENT,
    full_name VARCHAR(150) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    `role` ENUM('ADMIN','HR','MANAGER','EMPLOYEE') NOT NULL,
    is_active BOOL NOT NULL DEFAULT 1,
    last_login_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_users PRIMARY KEY (id),
    CONSTRAINT uq_users_email UNIQUE (email)
);

CREATE INDEX ix_users_role ON users (`role`);

CREATE TABLE audit_logs (
    id INTEGER NOT NULL AUTO_INCREMENT,
    actor_user_id INTEGER,
    action VARCHAR(100) NOT NULL,
    entity_type VARCHAR(100) NOT NULL,
    entity_id VARCHAR(64),
    metadata JSON,
    created_at DATETIME NOT NULL,
    CONSTRAINT pk_audit_logs PRIMARY KEY (id),
    CONSTRAINT fk_audit_logs_actor_user_id_users FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_audit_logs_action ON audit_logs (action);

CREATE INDEX ix_audit_logs_actor_user_id ON audit_logs (actor_user_id);

CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at);

CREATE INDEX ix_audit_logs_entity ON audit_logs (entity_type, entity_id);

CREATE TABLE employees (
    id INTEGER NOT NULL AUTO_INCREMENT,
    employee_code VARCHAR(20) NOT NULL,
    user_id INTEGER,
    full_name VARCHAR(150) NOT NULL,
    work_email VARCHAR(255) NOT NULL,
    department_id INTEGER NOT NULL,
    designation VARCHAR(100) NOT NULL,
    date_of_joining DATE NOT NULL,
    status ENUM('ACTIVE','INACTIVE','ON_LEAVE') NOT NULL,
    phone VARCHAR(20),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_employees PRIMARY KEY (id),
    CONSTRAINT fk_employees_department_id_departments FOREIGN KEY(department_id) REFERENCES departments (id) ON DELETE RESTRICT,
    CONSTRAINT fk_employees_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT uq_employees_employee_code UNIQUE (employee_code),
    CONSTRAINT uq_employees_user_id UNIQUE (user_id),
    CONSTRAINT uq_employees_work_email UNIQUE (work_email)
);

CREATE INDEX ix_employees_department_id ON employees (department_id);

CREATE INDEX ix_employees_status ON employees (status);

INSERT INTO alembic_version (version_num) VALUES ('14cb546c9a5e');

-- Running upgrade 14cb546c9a5e -> 319083e42c0c

CREATE TABLE shifts (
    id INTEGER NOT NULL AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    description TEXT,
    break_minutes INTEGER NOT NULL,
    overtime_threshold_minutes INTEGER NOT NULL,
    is_active BOOL NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_shifts PRIMARY KEY (id),
    CONSTRAINT ck_shifts_break_minutes_range CHECK (break_minutes >= 0 AND break_minutes <= 240),
    CONSTRAINT ck_shifts_overtime_threshold_range CHECK (overtime_threshold_minutes > 0 AND overtime_threshold_minutes <= 1440),
    CONSTRAINT ck_shifts_distinct_times CHECK (start_time <> end_time),
    CONSTRAINT uq_shifts_name UNIQUE (name)
);

CREATE TABLE attendance_records (
    id INTEGER NOT NULL AUTO_INCREMENT,
    employee_id INTEGER NOT NULL,
    shift_id INTEGER,
    work_date DATE NOT NULL,
    check_in DATETIME,
    check_out DATETIME,
    break_minutes INTEGER NOT NULL,
    work_minutes INTEGER,
    overtime_minutes INTEGER,
    status ENUM('PRESENT','LATE','HALF_DAY','ABSENT','ON_LEAVE') NOT NULL,
    note VARCHAR(500),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_attendance_records PRIMARY KEY (id),
    CONSTRAINT ck_attendance_records_break_minutes_non_negative CHECK (break_minutes >= 0),
    CONSTRAINT ck_attendance_records_checkout_after_checkin CHECK (check_out IS NULL OR (check_in IS NOT NULL AND check_out > check_in)),
    CONSTRAINT ck_attendance_records_overtime_minutes_non_negative CHECK (overtime_minutes IS NULL OR overtime_minutes >= 0),
    CONSTRAINT ck_attendance_records_work_minutes_non_negative CHECK (work_minutes IS NULL OR work_minutes >= 0),
    CONSTRAINT fk_attendance_records_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE RESTRICT,
    CONSTRAINT fk_attendance_records_shift_id_shifts FOREIGN KEY(shift_id) REFERENCES shifts (id) ON DELETE RESTRICT,
    CONSTRAINT uq_attendance_records_employee_id_work_date UNIQUE (employee_id, work_date)
);

CREATE INDEX ix_attendance_records_shift_id ON attendance_records (shift_id);

CREATE INDEX ix_attendance_records_status ON attendance_records (status);

CREATE INDEX ix_attendance_records_work_date ON attendance_records (work_date);

UPDATE alembic_version SET version_num='319083e42c0c' WHERE alembic_version.version_num = '14cb546c9a5e';

-- Running upgrade 319083e42c0c -> da1930de632b

CREATE TABLE leave_types (
    id INTEGER NOT NULL AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    code VARCHAR(20) NOT NULL,
    description TEXT,
    is_paid BOOL NOT NULL DEFAULT 1,
    is_active BOOL NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_leave_types PRIMARY KEY (id),
    CONSTRAINT uq_leave_types_code UNIQUE (code),
    CONSTRAINT uq_leave_types_name UNIQUE (name)
);

CREATE TABLE leave_policies (
    id INTEGER NOT NULL AUTO_INCREMENT,
    leave_type_id INTEGER NOT NULL,
    department_id INTEGER,
    annual_allowance_days INTEGER NOT NULL,
    max_consecutive_days INTEGER,
    carry_forward_days INTEGER NOT NULL,
    count_weekends BOOL NOT NULL DEFAULT 0,
    effective_from DATE NOT NULL,
    effective_to DATE,
    is_active BOOL NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_leave_policies PRIMARY KEY (id),
    CONSTRAINT ck_leave_policies_allowance_range CHECK (annual_allowance_days >= 0 AND annual_allowance_days <= 366),
    CONSTRAINT ck_leave_policies_carry_forward_range CHECK (carry_forward_days >= 0 AND carry_forward_days <= 366),
    CONSTRAINT ck_leave_policies_effective_range CHECK (effective_to IS NULL OR effective_to >= effective_from),
    CONSTRAINT ck_leave_policies_max_consecutive_range CHECK (max_consecutive_days IS NULL OR (max_consecutive_days > 0 AND max_consecutive_days <= 366)),
    CONSTRAINT fk_leave_policies_department_id_departments FOREIGN KEY(department_id) REFERENCES departments (id) ON DELETE RESTRICT,
    CONSTRAINT fk_leave_policies_leave_type_id_leave_types FOREIGN KEY(leave_type_id) REFERENCES leave_types (id) ON DELETE RESTRICT
);

CREATE INDEX ix_leave_policies_type_department ON leave_policies (leave_type_id, department_id);

CREATE TABLE notifications (
    id INTEGER NOT NULL AUTO_INCREMENT,
    user_id INTEGER NOT NULL,
    category VARCHAR(50) NOT NULL,
    title VARCHAR(150) NOT NULL,
    message VARCHAR(500) NOT NULL,
    entity_type VARCHAR(50),
    entity_id VARCHAR(64),
    is_read BOOL NOT NULL DEFAULT 0,
    read_at DATETIME,
    created_at DATETIME NOT NULL,
    CONSTRAINT pk_notifications PRIMARY KEY (id),
    CONSTRAINT fk_notifications_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_notifications_created_at ON notifications (created_at);

CREATE INDEX ix_notifications_user_unread ON notifications (user_id, is_read);

CREATE TABLE leave_balances (
    id INTEGER NOT NULL AUTO_INCREMENT,
    employee_id INTEGER NOT NULL,
    leave_type_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    allocated_days INTEGER NOT NULL,
    carried_forward_days INTEGER NOT NULL,
    used_days INTEGER NOT NULL,
    pending_days INTEGER NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_leave_balances PRIMARY KEY (id),
    CONSTRAINT ck_leave_balances_allocated_non_negative CHECK (allocated_days >= 0),
    CONSTRAINT ck_leave_balances_carried_non_negative CHECK (carried_forward_days >= 0),
    CONSTRAINT ck_leave_balances_pending_non_negative CHECK (pending_days >= 0),
    CONSTRAINT ck_leave_balances_no_overspend CHECK (used_days + pending_days <= allocated_days + carried_forward_days),
    CONSTRAINT ck_leave_balances_used_non_negative CHECK (used_days >= 0),
    CONSTRAINT ck_leave_balances_year_range CHECK (year >= 2000 AND year <= 2100),
    CONSTRAINT fk_leave_balances_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE RESTRICT,
    CONSTRAINT fk_leave_balances_leave_type_id_leave_types FOREIGN KEY(leave_type_id) REFERENCES leave_types (id) ON DELETE RESTRICT,
    CONSTRAINT uq_leave_balances_employee_id_leave_type_id_year UNIQUE (employee_id, leave_type_id, year)
);

CREATE INDEX ix_leave_balances_leave_type_id ON leave_balances (leave_type_id);

CREATE TABLE leave_requests (
    id INTEGER NOT NULL AUTO_INCREMENT,
    employee_id INTEGER NOT NULL,
    leave_type_id INTEGER NOT NULL,
    policy_id INTEGER NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    working_days INTEGER NOT NULL,
    reason VARCHAR(500) NOT NULL,
    status ENUM('PENDING','APPROVED','REJECTED','CANCELLED') NOT NULL,
    reviewed_by_user_id INTEGER,
    reviewed_at DATETIME,
    review_comment VARCHAR(500),
    cancelled_by_user_id INTEGER,
    cancelled_at DATETIME,
    cancellation_comment VARCHAR(500),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_leave_requests PRIMARY KEY (id),
    CONSTRAINT ck_leave_requests_date_range CHECK (end_date >= start_date),
    CONSTRAINT ck_leave_requests_working_days_range CHECK (working_days > 0 AND working_days <= 366),
    CONSTRAINT fk_leave_requests_cancelled_by_user_id_users FOREIGN KEY(cancelled_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_leave_requests_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE RESTRICT,
    CONSTRAINT fk_leave_requests_leave_type_id_leave_types FOREIGN KEY(leave_type_id) REFERENCES leave_types (id) ON DELETE RESTRICT,
    CONSTRAINT fk_leave_requests_policy_id_leave_policies FOREIGN KEY(policy_id) REFERENCES leave_policies (id) ON DELETE RESTRICT,
    CONSTRAINT fk_leave_requests_reviewed_by_user_id_users FOREIGN KEY(reviewed_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_leave_requests_employee_dates ON leave_requests (employee_id, start_date, end_date);

CREATE INDEX ix_leave_requests_leave_type_id ON leave_requests (leave_type_id);

CREATE INDEX ix_leave_requests_status ON leave_requests (status);

UPDATE alembic_version SET version_num='da1930de632b' WHERE alembic_version.version_num = '319083e42c0c';

-- Running upgrade da1930de632b -> a28868d7048a

CREATE TABLE salary_components (
    id INTEGER NOT NULL AUTO_INCREMENT,
    code VARCHAR(30) NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    kind ENUM('EARNING','DEDUCTION','TAX') NOT NULL,
    calculation_method ENUM('FIXED','PERCENT_OF_BASE','PERCENT_OF_GROSS') NOT NULL,
    display_order INTEGER NOT NULL,
    is_active BOOL NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_salary_components PRIMARY KEY (id),
    CONSTRAINT ck_salary_components_earning_not_percent_of_gross CHECK (NOT (kind = 'EARNING' AND calculation_method = 'PERCENT_OF_GROSS')),
    CONSTRAINT uq_salary_components_code UNIQUE (code),
    CONSTRAINT uq_salary_components_name UNIQUE (name)
);

CREATE TABLE payroll_periods (
    id INTEGER NOT NULL AUTO_INCREMENT,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    created_by_user_id INTEGER,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_payroll_periods PRIMARY KEY (id),
    CONSTRAINT ck_payroll_periods_date_range CHECK (end_date >= start_date),
    CONSTRAINT ck_payroll_periods_month_range CHECK (month >= 1 AND month <= 12),
    CONSTRAINT ck_payroll_periods_year_range CHECK (year >= 2000 AND year <= 2100),
    CONSTRAINT fk_payroll_periods_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT uq_payroll_periods_year_month UNIQUE (year, month)
);

CREATE TABLE payroll_profiles (
    id INTEGER NOT NULL AUTO_INCREMENT,
    employee_id INTEGER NOT NULL,
    pay_frequency ENUM('MONTHLY') NOT NULL,
    base_salary NUMERIC(12, 2) NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    is_active BOOL NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_payroll_profiles PRIMARY KEY (id),
    CONSTRAINT ck_payroll_profiles_base_salary_positive CHECK (base_salary > 0),
    CONSTRAINT ck_payroll_profiles_effective_range CHECK (effective_to IS NULL OR effective_to >= effective_from),
    CONSTRAINT fk_payroll_profiles_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE RESTRICT
);

CREATE INDEX ix_payroll_profiles_employee_effective ON payroll_profiles (employee_id, effective_from);

CREATE TABLE payroll_profile_components (
    id INTEGER NOT NULL AUTO_INCREMENT,
    profile_id INTEGER NOT NULL,
    component_id INTEGER NOT NULL,
    amount NUMERIC(12, 2),
    percentage NUMERIC(5, 2),
    CONSTRAINT pk_payroll_profile_components PRIMARY KEY (id),
    CONSTRAINT ck_payroll_profile_components_amount_xor_percentage CHECK ((amount IS NULL) <> (percentage IS NULL)),
    CONSTRAINT ck_payroll_profile_components_amount_non_negative CHECK (amount IS NULL OR amount >= 0),
    CONSTRAINT ck_payroll_profile_components_percentage_range CHECK (percentage IS NULL OR (percentage >= 0 AND percentage <= 100)),
    CONSTRAINT fk_payroll_profile_components_component_id_salary_components FOREIGN KEY(component_id) REFERENCES salary_components (id) ON DELETE RESTRICT,
    CONSTRAINT fk_payroll_profile_components_profile_id_payroll_profiles FOREIGN KEY(profile_id) REFERENCES payroll_profiles (id) ON DELETE CASCADE,
    CONSTRAINT uq_payroll_profile_components_profile_id_component_id UNIQUE (profile_id, component_id)
);

CREATE INDEX ix_payroll_profile_components_component_id ON payroll_profile_components (component_id);

CREATE TABLE payroll_records (
    id INTEGER NOT NULL AUTO_INCREMENT,
    period_id INTEGER NOT NULL,
    employee_id INTEGER NOT NULL,
    profile_id INTEGER NOT NULL,
    status ENUM('CALCULATED','APPROVED','PAID') NOT NULL,
    currency VARCHAR(3) NOT NULL,
    base_salary NUMERIC(12, 2) NOT NULL,
    gross_earnings NUMERIC(12, 2) NOT NULL,
    total_deductions NUMERIC(12, 2) NOT NULL,
    net_pay NUMERIC(12, 2) NOT NULL,
    warnings JSON,
    calculated_at DATETIME NOT NULL,
    calculated_by_user_id INTEGER,
    approved_at DATETIME,
    approved_by_user_id INTEGER,
    paid_at DATETIME,
    paid_by_user_id INTEGER,
    payment_reference VARCHAR(100),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_payroll_records PRIMARY KEY (id),
    CONSTRAINT ck_payroll_records_base_salary_positive CHECK (base_salary > 0),
    CONSTRAINT ck_payroll_records_gross_non_negative CHECK (gross_earnings >= 0),
    CONSTRAINT ck_payroll_records_net_is_gross_minus_deductions CHECK (ABS(net_pay - (gross_earnings - total_deductions)) < 0.005),
    CONSTRAINT ck_payroll_records_net_non_negative CHECK (net_pay >= 0),
    CONSTRAINT ck_payroll_records_deductions_non_negative CHECK (total_deductions >= 0),
    CONSTRAINT fk_payroll_records_approved_by_user_id_users FOREIGN KEY(approved_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_payroll_records_calculated_by_user_id_users FOREIGN KEY(calculated_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_payroll_records_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE RESTRICT,
    CONSTRAINT fk_payroll_records_paid_by_user_id_users FOREIGN KEY(paid_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_payroll_records_period_id_payroll_periods FOREIGN KEY(period_id) REFERENCES payroll_periods (id) ON DELETE RESTRICT,
    CONSTRAINT fk_payroll_records_profile_id_payroll_profiles FOREIGN KEY(profile_id) REFERENCES payroll_profiles (id) ON DELETE RESTRICT,
    CONSTRAINT uq_payroll_records_period_id_employee_id UNIQUE (period_id, employee_id)
);

CREATE INDEX ix_payroll_records_employee_period ON payroll_records (employee_id, period_id);

CREATE INDEX ix_payroll_records_status ON payroll_records (status);

CREATE TABLE payroll_line_items (
    id INTEGER NOT NULL AUTO_INCREMENT,
    record_id INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    kind ENUM('EARNING','DEDUCTION','TAX') NOT NULL,
    code VARCHAR(30) NOT NULL,
    name VARCHAR(100) NOT NULL,
    calculation_method VARCHAR(30) NOT NULL,
    rate NUMERIC(5, 2),
    basis NUMERIC(12, 2),
    quantity NUMERIC(7, 2),
    amount NUMERIC(12, 2) NOT NULL,
    detail JSON,
    CONSTRAINT pk_payroll_line_items PRIMARY KEY (id),
    CONSTRAINT ck_payroll_line_items_amount_non_negative CHECK (amount >= 0),
    CONSTRAINT ck_payroll_line_items_rate_range CHECK (rate IS NULL OR (rate >= 0 AND rate <= 100)),
    CONSTRAINT fk_payroll_line_items_record_id_payroll_records FOREIGN KEY(record_id) REFERENCES payroll_records (id) ON DELETE CASCADE,
    CONSTRAINT uq_payroll_line_items_record_id_sequence UNIQUE (record_id, sequence)
);

UPDATE alembic_version SET version_num='a28868d7048a' WHERE alembic_version.version_num = 'da1930de632b';

-- Running upgrade a28868d7048a -> 371a76ea7369

CREATE TABLE attrition_model_versions (
    id INTEGER NOT NULL AUTO_INCREMENT,
    model_version VARCHAR(40) NOT NULL,
    feature_schema_version VARCHAR(40) NOT NULL,
    status ENUM('CANDIDATE','ACTIVE','REJECTED','RETIRED') NOT NULL,
    trained_at DATETIME NOT NULL,
    trained_by_user_id INTEGER,
    dataset_fingerprint VARCHAR(64) NOT NULL,
    artifact_sha256 VARCHAR(64) NOT NULL,
    training_rows INTEGER NOT NULL,
    evaluation JSON NOT NULL,
    activated_at DATETIME,
    activated_by_user_id INTEGER,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_attrition_model_versions PRIMARY KEY (id),
    CONSTRAINT fk_attrition_model_versions_activated_by_user_id_users FOREIGN KEY(activated_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_attrition_model_versions_trained_by_user_id_users FOREIGN KEY(trained_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT uq_attrition_model_versions_model_version UNIQUE (model_version)
);

CREATE INDEX ix_attrition_model_versions_status ON attrition_model_versions (status);

CREATE TABLE attrition_predictions (
    id INTEGER NOT NULL AUTO_INCREMENT,
    employee_id INTEGER NOT NULL,
    model_version_id INTEGER NOT NULL,
    feature_schema_version VARCHAR(40) NOT NULL,
    risk_score NUMERIC(5, 2) NOT NULL,
    risk_category ENUM('LOW','MEDIUM','HIGH') NOT NULL,
    features JSON NOT NULL,
    explanation JSON,
    predicted_at DATETIME NOT NULL,
    requested_by_user_id INTEGER,
    batch_id VARCHAR(36),
    CONSTRAINT pk_attrition_predictions PRIMARY KEY (id),
    CONSTRAINT ck_attrition_predictions_risk_score_range CHECK (risk_score >= 0 AND risk_score <= 100),
    CONSTRAINT fk_attrition_predictions_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE RESTRICT,
    CONSTRAINT fk_attrition_predictions_model_version_id_attrition_mode_3d16 FOREIGN KEY(model_version_id) REFERENCES attrition_model_versions (id) ON DELETE RESTRICT,
    CONSTRAINT fk_attrition_predictions_requested_by_user_id_users FOREIGN KEY(requested_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_attrition_predictions_batch_id ON attrition_predictions (batch_id);

CREATE INDEX ix_attrition_predictions_employee_predicted ON attrition_predictions (employee_id, predicted_at);

CREATE INDEX ix_attrition_predictions_model_version_id ON attrition_predictions (model_version_id);

CREATE INDEX ix_attrition_predictions_risk_category ON attrition_predictions (risk_category);

UPDATE alembic_version SET version_num='371a76ea7369' WHERE alembic_version.version_num = 'a28868d7048a';

-- Running upgrade 371a76ea7369 -> 66d767384f54

CREATE TABLE datasets (
    id INTEGER NOT NULL AUTO_INCREMENT,
    display_name VARCHAR(100) NOT NULL,
    purpose ENUM('ATTRITION_TRAINING') NOT NULL,
    created_by_user_id INTEGER,
    archived_at DATETIME,
    archived_by_user_id INTEGER,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_datasets PRIMARY KEY (id),
    CONSTRAINT fk_datasets_archived_by_user_id_users FOREIGN KEY(archived_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_datasets_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE TABLE dataset_versions (
    id INTEGER NOT NULL AUTO_INCREMENT,
    dataset_id INTEGER NOT NULL,
    version_number INTEGER NOT NULL,
    storage_key VARCHAR(32) NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    size_bytes INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    column_count INTEGER NOT NULL,
    status ENUM('UPLOADED','VALIDATING','VALIDATED','REJECTED','ARCHIVED') NOT NULL,
    schema_version VARCHAR(40),
    training_eligible BOOL NOT NULL DEFAULT 0,
    validation_report JSON,
    validation_started_at DATETIME,
    validated_at DATETIME,
    uploaded_by_user_id INTEGER,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_dataset_versions PRIMARY KEY (id),
    CONSTRAINT ck_dataset_versions_shape_valid CHECK (row_count >= 0 AND column_count > 0),
    CONSTRAINT ck_dataset_versions_size_positive CHECK (size_bytes > 0),
    CONSTRAINT ck_dataset_versions_version_positive CHECK (version_number >= 1),
    CONSTRAINT fk_dataset_versions_dataset_id_datasets FOREIGN KEY(dataset_id) REFERENCES datasets (id) ON DELETE RESTRICT,
    CONSTRAINT fk_dataset_versions_uploaded_by_user_id_users FOREIGN KEY(uploaded_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT uq_dataset_versions_dataset_id_version_number UNIQUE (dataset_id, version_number),
    CONSTRAINT uq_dataset_versions_sha256 UNIQUE (sha256),
    CONSTRAINT uq_dataset_versions_storage_key UNIQUE (storage_key)
);

CREATE INDEX ix_dataset_versions_status ON dataset_versions (status);

ALTER TABLE attrition_model_versions ADD COLUMN dataset_version_id INTEGER;

CREATE INDEX ix_attrition_model_versions_dataset_version_id ON attrition_model_versions (dataset_version_id);

ALTER TABLE attrition_model_versions ADD CONSTRAINT fk_attrition_model_versions_dataset_version_id_dataset_versions FOREIGN KEY(dataset_version_id) REFERENCES dataset_versions (id) ON DELETE RESTRICT;

UPDATE alembic_version SET version_num='66d767384f54' WHERE alembic_version.version_num = '371a76ea7369';

-- Running upgrade 66d767384f54 -> 03f5784d8edc

CREATE TABLE risk_alerts (
    id INTEGER NOT NULL AUTO_INCREMENT,
    employee_id INTEGER NOT NULL,
    trigger_type ENUM('THRESHOLD_CROSSED','SCORE_INCREASE','SCORE_DECREASE','STALE_PREDICTION') NOT NULL,
    priority ENUM('LOW','MEDIUM','HIGH') NOT NULL,
    status ENUM('OPEN','ACKNOWLEDGED','RESOLVED','DISMISSED') NOT NULL,
    latest_prediction_id INTEGER NOT NULL,
    latest_score NUMERIC(5, 2) NOT NULL,
    latest_category ENUM('LOW','MEDIUM','HIGH') NOT NULL,
    model_version VARCHAR(40) NOT NULL,
    event_count INTEGER NOT NULL,
    first_detected_at DATETIME NOT NULL,
    last_observed_at DATETIME NOT NULL,
    assigned_to_user_id INTEGER,
    acknowledged_at DATETIME,
    acknowledged_by_user_id INTEGER,
    resolved_at DATETIME,
    resolved_by_user_id INTEGER,
    resolution_reason VARCHAR(500),
    reviewer_notes VARCHAR(1000),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_risk_alerts PRIMARY KEY (id),
    CONSTRAINT ck_risk_alerts_event_count_positive CHECK (event_count >= 1),
    CONSTRAINT ck_risk_alerts_latest_score_range CHECK (latest_score >= 0 AND latest_score <= 100),
    CONSTRAINT fk_risk_alerts_acknowledged_by_user_id_users FOREIGN KEY(acknowledged_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_risk_alerts_assigned_to_user_id_users FOREIGN KEY(assigned_to_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_risk_alerts_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE RESTRICT,
    CONSTRAINT fk_risk_alerts_latest_prediction_id_attrition_predictions FOREIGN KEY(latest_prediction_id) REFERENCES attrition_predictions (id) ON DELETE RESTRICT,
    CONSTRAINT fk_risk_alerts_resolved_by_user_id_users FOREIGN KEY(resolved_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_risk_alerts_assigned_to_user_id ON risk_alerts (assigned_to_user_id);

CREATE INDEX ix_risk_alerts_employee_status ON risk_alerts (employee_id, status);

CREATE INDEX ix_risk_alerts_priority ON risk_alerts (priority);

CREATE INDEX ix_risk_alerts_status ON risk_alerts (status);

CREATE TABLE risk_events (
    id INTEGER NOT NULL AUTO_INCREMENT,
    employee_id INTEGER NOT NULL,
    event_type ENUM('THRESHOLD_CROSSED','SCORE_INCREASE','SCORE_DECREASE','STALE_PREDICTION') NOT NULL,
    dedup_key VARCHAR(100) NOT NULL,
    current_prediction_id INTEGER NOT NULL,
    previous_prediction_id INTEGER,
    current_score NUMERIC(5, 2) NOT NULL,
    previous_score NUMERIC(5, 2),
    current_category ENUM('LOW','MEDIUM','HIGH') NOT NULL,
    previous_category ENUM('LOW','MEDIUM','HIGH'),
    model_version VARCHAR(40) NOT NULL,
    detected_at DATETIME NOT NULL,
    alert_id INTEGER,
    CONSTRAINT pk_risk_events PRIMARY KEY (id),
    CONSTRAINT ck_risk_events_current_score_range CHECK (current_score >= 0 AND current_score <= 100),
    CONSTRAINT ck_risk_events_previous_score_range CHECK (previous_score IS NULL OR (previous_score >= 0 AND previous_score <= 100)),
    CONSTRAINT fk_risk_events_alert_id_risk_alerts FOREIGN KEY(alert_id) REFERENCES risk_alerts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_risk_events_current_prediction_id_attrition_predictions FOREIGN KEY(current_prediction_id) REFERENCES attrition_predictions (id) ON DELETE RESTRICT,
    CONSTRAINT fk_risk_events_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE RESTRICT,
    CONSTRAINT fk_risk_events_previous_prediction_id_attrition_predictions FOREIGN KEY(previous_prediction_id) REFERENCES attrition_predictions (id) ON DELETE RESTRICT,
    CONSTRAINT uq_risk_events_dedup_key UNIQUE (dedup_key)
);

CREATE INDEX ix_risk_events_alert_id ON risk_events (alert_id);

CREATE INDEX ix_risk_events_employee_detected ON risk_events (employee_id, detected_at);

CREATE INDEX ix_risk_events_event_type ON risk_events (event_type);

UPDATE alembic_version SET version_num='03f5784d8edc' WHERE alembic_version.version_num = '66d767384f54';

-- Running upgrade 03f5784d8edc -> 70dd4dec2e60

CREATE TABLE workforce_snapshots (
    id INTEGER NOT NULL AUTO_INCREMENT,
    snapshot_date DATE NOT NULL,
    captured_at DATETIME NOT NULL,
    captured_by_user_id INTEGER,
    period_start DATE NOT NULL,
    total_employees INTEGER NOT NULL,
    active_employees INTEGER NOT NULL,
    on_leave_employees INTEGER NOT NULL,
    inactive_employees INTEGER NOT NULL,
    joiners_in_period INTEGER NOT NULL,
    recorded_departures_in_period INTEGER NOT NULL,
    source VARCHAR(50) NOT NULL,
    data_quality JSON NOT NULL,
    CONSTRAINT pk_workforce_snapshots PRIMARY KEY (id),
    CONSTRAINT ck_workforce_snapshots_period_valid CHECK (period_start <= snapshot_date),
    CONSTRAINT ck_workforce_snapshots_counts_valid CHECK (total_employees >= 0 AND active_employees >= 0 AND on_leave_employees >= 0 AND inactive_employees >= 0 AND joiners_in_period >= 0 AND recorded_departures_in_period >= 0 AND active_employees + on_leave_employees + inactive_employees = total_employees),
    CONSTRAINT fk_workforce_snapshots_captured_by_user_id_users FOREIGN KEY(captured_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT uq_workforce_snapshots_snapshot_date UNIQUE (snapshot_date)
);

CREATE TABLE workforce_snapshot_departments (
    id INTEGER NOT NULL AUTO_INCREMENT,
    snapshot_id INTEGER NOT NULL,
    department_id INTEGER NOT NULL,
    department_name VARCHAR(100) NOT NULL,
    total_employees INTEGER NOT NULL,
    active_employees INTEGER NOT NULL,
    on_leave_employees INTEGER NOT NULL,
    inactive_employees INTEGER NOT NULL,
    joiners_in_period INTEGER NOT NULL,
    recorded_departures_in_period INTEGER NOT NULL,
    CONSTRAINT pk_workforce_snapshot_departments PRIMARY KEY (id),
    CONSTRAINT ck_workforce_snapshot_departments_counts_valid CHECK (total_employees >= 0 AND active_employees >= 0 AND on_leave_employees >= 0 AND inactive_employees >= 0 AND joiners_in_period >= 0 AND recorded_departures_in_period >= 0 AND active_employees + on_leave_employees + inactive_employees = total_employees),
    CONSTRAINT fk_workforce_snapshot_departments_department_id_departments FOREIGN KEY(department_id) REFERENCES departments (id) ON DELETE RESTRICT,
    CONSTRAINT fk_workforce_snapshot_departments_snapshot_id_workforce__26bc FOREIGN KEY(snapshot_id) REFERENCES workforce_snapshots (id) ON DELETE RESTRICT,
    CONSTRAINT uq_workforce_snapshot_departments_snapshot_id_department_id UNIQUE (snapshot_id, department_id)
);

CREATE INDEX ix_workforce_snapshot_departments_department_id ON workforce_snapshot_departments (department_id);

UPDATE alembic_version SET version_num='70dd4dec2e60' WHERE alembic_version.version_num = '03f5784d8edc';

-- Running upgrade 70dd4dec2e60 -> 87cc35d2de25

CREATE TABLE interventions (
    id INTEGER NOT NULL AUTO_INCREMENT,
    employee_id INTEGER NOT NULL,
    risk_alert_id INTEGER,
    category ENUM('MANAGER_CHECK_IN','WORKLOAD_REVIEW','CAREER_DEVELOPMENT','RECOGNITION','LEARNING_SUPPORT','WORK_ENVIRONMENT_REVIEW','OTHER_SUPPORT') NOT NULL,
    title VARCHAR(150) NOT NULL,
    description VARCHAR(1000),
    priority ENUM('LOW','MEDIUM','HIGH') NOT NULL,
    status ENUM('OPEN','IN_PROGRESS','COMPLETED','CANCELLED') NOT NULL,
    assigned_to_user_id INTEGER,
    due_date DATE,
    started_at DATETIME,
    completed_at DATETIME,
    outcome VARCHAR(1000),
    cancelled_at DATETIME,
    cancellation_reason VARCHAR(500),
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER,
    version INTEGER NOT NULL,
    active_alert_key VARCHAR(80),
    idempotency_key VARCHAR(64),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_interventions PRIMARY KEY (id),
    CONSTRAINT ck_interventions_cancelled_has_reason CHECK (status <> 'CANCELLED' OR (cancelled_at IS NOT NULL AND cancellation_reason IS NOT NULL)),
    CONSTRAINT ck_interventions_completed_has_outcome CHECK (status <> 'COMPLETED' OR (completed_at IS NOT NULL AND outcome IS NOT NULL)),
    CONSTRAINT ck_interventions_inactive_has_no_key CHECK (status IN ('OPEN', 'IN_PROGRESS') OR active_alert_key IS NULL),
    CONSTRAINT ck_interventions_version_positive CHECK (version >= 1),
    CONSTRAINT fk_interventions_assigned_to_user_id_users FOREIGN KEY(assigned_to_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_interventions_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_interventions_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE RESTRICT,
    CONSTRAINT fk_interventions_risk_alert_id_risk_alerts FOREIGN KEY(risk_alert_id) REFERENCES risk_alerts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_interventions_updated_by_user_id_users FOREIGN KEY(updated_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT uq_interventions_active_alert_key UNIQUE (active_alert_key),
    CONSTRAINT uq_interventions_idempotency_key UNIQUE (idempotency_key)
);

CREATE INDEX ix_interventions_assigned_to_user_id ON interventions (assigned_to_user_id);

CREATE INDEX ix_interventions_employee_status ON interventions (employee_id, status);

CREATE INDEX ix_interventions_priority ON interventions (priority);

CREATE INDEX ix_interventions_risk_alert_id ON interventions (risk_alert_id);

CREATE INDEX ix_interventions_status ON interventions (status);

CREATE TABLE intervention_events (
    id INTEGER NOT NULL AUTO_INCREMENT,
    intervention_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    event_type ENUM('CREATED','UPDATED','ASSIGNED','STARTED','COMPLETED','CANCELLED','NOTE_ADDED') NOT NULL,
    from_status ENUM('OPEN','IN_PROGRESS','COMPLETED','CANCELLED'),
    to_status ENUM('OPEN','IN_PROGRESS','COMPLETED','CANCELLED'),
    actor_user_id INTEGER,
    note VARCHAR(2000),
    details JSON,
    created_at DATETIME NOT NULL,
    CONSTRAINT pk_intervention_events PRIMARY KEY (id),
    CONSTRAINT fk_intervention_events_actor_user_id_users FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_intervention_events_intervention_id_interventions FOREIGN KEY(intervention_id) REFERENCES interventions (id) ON DELETE RESTRICT,
    CONSTRAINT uq_intervention_events_intervention_id_version UNIQUE (intervention_id, version)
);

UPDATE alembic_version SET version_num='87cc35d2de25' WHERE alembic_version.version_num = '70dd4dec2e60';

-- Running upgrade 87cc35d2de25 -> a6382858faa9

CREATE TABLE recommendations (
    id INTEGER NOT NULL AUTO_INCREMENT,
    rule_id VARCHAR(50) NOT NULL,
    rule_version VARCHAR(20) NOT NULL,
    scope ENUM('INDIVIDUAL','DEPARTMENT','ORGANIZATION') NOT NULL,
    category ENUM('SUPPORT_CHECK_IN','WORKLOAD_REVIEW','CAREER_DEVELOPMENT','DEPARTMENT_RETENTION_REVIEW','WORKFORCE_TREND_REVIEW','DATA_QUALITY') NOT NULL,
    priority ENUM('LOW','MEDIUM','HIGH') NOT NULL,
    status ENUM('AVAILABLE','ACCEPTED','DISMISSED','COMPLETED') NOT NULL,
    employee_id INTEGER,
    department_id INTEGER,
    risk_alert_id INTEGER,
    intervention_id INTEGER,
    title VARCHAR(150) NOT NULL,
    suggested_action VARCHAR(500) NOT NULL,
    explanation VARCHAR(1000) NOT NULL,
    evidence JSON NOT NULL,
    evidence_window VARCHAR(60) NOT NULL,
    source_data_as_of DATETIME,
    evaluated_at DATETIME NOT NULL,
    dedup_key VARCHAR(200) NOT NULL,
    reviewed_by_user_id INTEGER,
    reviewed_at DATETIME,
    dismissal_reason VARCHAR(500),
    completion_outcome VARCHAR(1000),
    version INTEGER NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_recommendations PRIMARY KEY (id),
    CONSTRAINT ck_recommendations_scope_subject CHECK ((scope = 'INDIVIDUAL' AND employee_id IS NOT NULL) OR (scope = 'DEPARTMENT' AND employee_id IS NULL AND department_id IS NOT NULL) OR (scope = 'ORGANIZATION' AND employee_id IS NULL AND department_id IS NULL)),
    CONSTRAINT ck_recommendations_completed_has_outcome CHECK (status <> 'COMPLETED' OR completion_outcome IS NOT NULL),
    CONSTRAINT ck_recommendations_dismissed_has_reason CHECK (status <> 'DISMISSED' OR dismissal_reason IS NOT NULL),
    CONSTRAINT ck_recommendations_version_positive CHECK (version >= 1),
    CONSTRAINT fk_recommendations_department_id_departments FOREIGN KEY(department_id) REFERENCES departments (id) ON DELETE RESTRICT,
    CONSTRAINT fk_recommendations_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE RESTRICT,
    CONSTRAINT fk_recommendations_intervention_id_interventions FOREIGN KEY(intervention_id) REFERENCES interventions (id) ON DELETE RESTRICT,
    CONSTRAINT fk_recommendations_reviewed_by_user_id_users FOREIGN KEY(reviewed_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_recommendations_risk_alert_id_risk_alerts FOREIGN KEY(risk_alert_id) REFERENCES risk_alerts (id) ON DELETE RESTRICT,
    CONSTRAINT uq_recommendations_dedup_key UNIQUE (dedup_key)
);

CREATE INDEX ix_recommendations_department_id ON recommendations (department_id);

CREATE INDEX ix_recommendations_employee_id ON recommendations (employee_id);

CREATE INDEX ix_recommendations_risk_alert_id ON recommendations (risk_alert_id);

CREATE INDEX ix_recommendations_rule_id ON recommendations (rule_id);

CREATE INDEX ix_recommendations_scope_status ON recommendations (scope, status);

CREATE INDEX ix_recommendations_status ON recommendations (status);

UPDATE alembic_version SET version_num='a6382858faa9' WHERE alembic_version.version_num = '87cc35d2de25';

-- Running upgrade a6382858faa9 -> 0bc0f8d45e33

CREATE TABLE risk_alert_escalations (
    id INTEGER NOT NULL AUTO_INCREMENT,
    alert_id INTEGER NOT NULL,
    stage ENUM('APPROACHING','OVERDUE') NOT NULL,
    alert_priority ENUM('LOW','MEDIUM','HIGH') NOT NULL,
    review_due_at DATETIME NOT NULL,
    recipient_count INTEGER NOT NULL,
    triggered_by_user_id INTEGER,
    created_at DATETIME NOT NULL,
    CONSTRAINT pk_risk_alert_escalations PRIMARY KEY (id),
    CONSTRAINT ck_risk_alert_escalations_recipient_count_positive CHECK (recipient_count >= 1),
    CONSTRAINT fk_risk_alert_escalations_alert_id_risk_alerts FOREIGN KEY(alert_id) REFERENCES risk_alerts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_risk_alert_escalations_triggered_by_user_id_users FOREIGN KEY(triggered_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT uq_risk_alert_escalations_alert_id UNIQUE (alert_id, stage)
);

CREATE INDEX ix_risk_alert_escalations_created_at ON risk_alert_escalations (created_at);

UPDATE alembic_version SET version_num='0bc0f8d45e33' WHERE alembic_version.version_num = 'a6382858faa9';

-- Running upgrade 0bc0f8d45e33 -> 818f279b6dde

CREATE INDEX ix_attendance_records_created_at ON attendance_records (created_at);

CREATE INDEX ix_audit_logs_action_created_at ON audit_logs (action, created_at);

CREATE INDEX ix_employees_created_at ON employees (created_at);

CREATE INDEX ix_intervention_events_created_at ON intervention_events (created_at);

CREATE INDEX ix_leave_requests_cancelled_at ON leave_requests (cancelled_at);

CREATE INDEX ix_leave_requests_created_at ON leave_requests (created_at);

CREATE INDEX ix_leave_requests_reviewed_at ON leave_requests (reviewed_at);

CREATE INDEX ix_recommendations_evaluated_at ON recommendations (evaluated_at);

CREATE INDEX ix_recommendations_reviewed_at ON recommendations (reviewed_at);

CREATE INDEX ix_risk_alerts_acknowledged_at ON risk_alerts (acknowledged_at);

CREATE INDEX ix_risk_alerts_created_at ON risk_alerts (created_at);

CREATE INDEX ix_risk_alerts_resolved_at ON risk_alerts (resolved_at);

CREATE INDEX ix_risk_events_detected_at ON risk_events (detected_at);

UPDATE alembic_version SET version_num='818f279b6dde' WHERE alembic_version.version_num = '0bc0f8d45e33';

-- Running upgrade 818f279b6dde -> 0d5f1e6ae076

CREATE TABLE tasks (
    id INTEGER NOT NULL AUTO_INCREMENT,
    title VARCHAR(150) NOT NULL,
    description VARCHAR(2000),
    task_type ENUM('FOLLOW_UP','DOCUMENTATION','ONBOARDING','OFFBOARDING','TRAINING','REVIEW','COMPLIANCE','OTHER') NOT NULL,
    priority ENUM('LOW','MEDIUM','HIGH','CRITICAL') NOT NULL,
    status ENUM('OPEN','IN_PROGRESS','BLOCKED','COMPLETED','CANCELLED') NOT NULL,
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER,
    assignee_employee_id INTEGER,
    department_id INTEGER,
    due_date DATE,
    started_at DATETIME,
    completed_at DATETIME,
    completion_summary VARCHAR(2000),
    cancelled_at DATETIME,
    cancellation_reason VARCHAR(500),
    block_reason ENUM('WAITING_ON_EMPLOYEE','WAITING_ON_APPROVAL','WAITING_ON_INFORMATION','EXTERNAL_DEPENDENCY','OTHER'),
    block_details VARCHAR(500),
    intervention_id INTEGER,
    recommendation_id INTEGER,
    version INTEGER NOT NULL,
    idempotency_key VARCHAR(64),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_tasks PRIMARY KEY (id),
    CONSTRAINT ck_tasks_blocked_has_reason CHECK (status <> 'BLOCKED' OR block_reason IS NOT NULL),
    CONSTRAINT ck_tasks_cancelled_has_reason CHECK (status <> 'CANCELLED' OR (cancelled_at IS NOT NULL AND cancellation_reason IS NOT NULL)),
    CONSTRAINT ck_tasks_completed_has_time CHECK (status <> 'COMPLETED' OR completed_at IS NOT NULL),
    CONSTRAINT ck_tasks_single_source CHECK (intervention_id IS NULL OR recommendation_id IS NULL),
    CONSTRAINT ck_tasks_version_positive CHECK (version >= 1),
    CONSTRAINT fk_tasks_assignee_employee_id_employees FOREIGN KEY(assignee_employee_id) REFERENCES employees (id) ON DELETE RESTRICT,
    CONSTRAINT fk_tasks_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_tasks_department_id_departments FOREIGN KEY(department_id) REFERENCES departments (id) ON DELETE RESTRICT,
    CONSTRAINT fk_tasks_intervention_id_interventions FOREIGN KEY(intervention_id) REFERENCES interventions (id) ON DELETE RESTRICT,
    CONSTRAINT fk_tasks_recommendation_id_recommendations FOREIGN KEY(recommendation_id) REFERENCES recommendations (id) ON DELETE RESTRICT,
    CONSTRAINT fk_tasks_updated_by_user_id_users FOREIGN KEY(updated_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT uq_tasks_idempotency_key UNIQUE (idempotency_key)
);

CREATE INDEX ix_tasks_assignee_status ON tasks (assignee_employee_id, status);

CREATE INDEX ix_tasks_created_by_user_id ON tasks (created_by_user_id);

CREATE INDEX ix_tasks_department_status ON tasks (department_id, status);

CREATE INDEX ix_tasks_due_date ON tasks (due_date);

CREATE INDEX ix_tasks_intervention_id ON tasks (intervention_id);

CREATE INDEX ix_tasks_recommendation_id ON tasks (recommendation_id);

CREATE TABLE task_events (
    id INTEGER NOT NULL AUTO_INCREMENT,
    task_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    event_type ENUM('CREATED','UPDATED','ASSIGNED','STARTED','BLOCKED','UNBLOCKED','COMPLETED','CANCELLED','REOPENED') NOT NULL,
    from_status ENUM('OPEN','IN_PROGRESS','BLOCKED','COMPLETED','CANCELLED'),
    to_status ENUM('OPEN','IN_PROGRESS','BLOCKED','COMPLETED','CANCELLED'),
    actor_user_id INTEGER,
    details JSON,
    created_at DATETIME NOT NULL,
    CONSTRAINT pk_task_events PRIMARY KEY (id),
    CONSTRAINT fk_task_events_actor_user_id_users FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_task_events_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE RESTRICT,
    CONSTRAINT uq_task_events_task_id_version UNIQUE (task_id, version)
);

UPDATE alembic_version SET version_num='0d5f1e6ae076' WHERE alembic_version.version_num = '818f279b6dde';

-- Running upgrade 0d5f1e6ae076 -> b647981f350c

CREATE TABLE performance_cycles (
    id INTEGER NOT NULL AUTO_INCREMENT,
    name VARCHAR(120) NOT NULL,
    description VARCHAR(2000),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    self_assessment_deadline DATE NOT NULL,
    manager_review_deadline DATE NOT NULL,
    status ENUM('DRAFT','OPEN','IN_PROGRESS','COMPLETED','CANCELLED') NOT NULL,
    cancellation_reason VARCHAR(500),
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER,
    version INTEGER NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_performance_cycles PRIMARY KEY (id),
    CONSTRAINT ck_performance_cycles_cancelled_has_reason CHECK (status <> 'CANCELLED' OR cancellation_reason IS NOT NULL),
    CONSTRAINT ck_performance_cycles_period_ordered CHECK (period_start <= period_end),
    CONSTRAINT ck_performance_cycles_deadlines_ordered CHECK (self_assessment_deadline >= period_start AND self_assessment_deadline <= manager_review_deadline),
    CONSTRAINT ck_performance_cycles_version_positive CHECK (version >= 1),
    CONSTRAINT fk_performance_cycles_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_performance_cycles_updated_by_user_id_users FOREIGN KEY(updated_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT uq_performance_cycles_name UNIQUE (name)
);

CREATE INDEX ix_performance_cycles_status ON performance_cycles (status);

CREATE TABLE performance_goals (
    id INTEGER NOT NULL AUTO_INCREMENT,
    employee_id INTEGER NOT NULL,
    cycle_id INTEGER,
    title VARCHAR(150) NOT NULL,
    description VARCHAR(2000),
    category ENUM('BUSINESS','DEVELOPMENT','LEARNING','TEAM','OTHER') NOT NULL,
    measurable_target VARCHAR(255) NOT NULL,
    target_value NUMERIC(12, 2),
    unit VARCHAR(30),
    start_date DATE NOT NULL,
    target_date DATE NOT NULL,
    progress_percent SMALLINT NOT NULL,
    status ENUM('NOT_STARTED','IN_PROGRESS','AT_RISK','COMPLETED','CANCELLED') NOT NULL,
    completed_at DATETIME,
    manager_feedback VARCHAR(2000),
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER,
    version INTEGER NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_performance_goals PRIMARY KEY (id),
    CONSTRAINT ck_performance_goals_completed_rule CHECK (status <> 'COMPLETED' OR (progress_percent = 100 AND completed_at IS NOT NULL)),
    CONSTRAINT ck_performance_goals_progress_range CHECK (progress_percent >= 0 AND progress_percent <= 100),
    CONSTRAINT ck_performance_goals_dates_ordered CHECK (start_date <= target_date),
    CONSTRAINT ck_performance_goals_version_positive CHECK (version >= 1),
    CONSTRAINT fk_performance_goals_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_performance_goals_cycle_id_performance_cycles FOREIGN KEY(cycle_id) REFERENCES performance_cycles (id) ON DELETE RESTRICT,
    CONSTRAINT fk_performance_goals_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE RESTRICT,
    CONSTRAINT fk_performance_goals_updated_by_user_id_users FOREIGN KEY(updated_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_performance_goals_cycle_id ON performance_goals (cycle_id);

CREATE INDEX ix_performance_goals_employee_status ON performance_goals (employee_id, status);

CREATE TABLE performance_reviews (
    id INTEGER NOT NULL AUTO_INCREMENT,
    cycle_id INTEGER NOT NULL,
    employee_id INTEGER NOT NULL,
    reviewer_user_id INTEGER NOT NULL,
    status ENUM('ASSIGNED','SELF_SUBMITTED','MANAGER_SUBMITTED','FINALIZED','CANCELLED') NOT NULL,
    self_assessment TEXT,
    self_submitted_at DATETIME,
    manager_summary TEXT,
    strengths JSON,
    improvement_areas JSON,
    development_goals JSON,
    training_recommendations JSON,
    manager_private_notes VARCHAR(2000),
    overall_rating SMALLINT,
    manager_submitted_at DATETIME,
    finalized_at DATETIME,
    finalized_by_user_id INTEGER,
    cancelled_at DATETIME,
    cancellation_reason VARCHAR(500),
    reopen_reason VARCHAR(500),
    created_by_user_id INTEGER,
    updated_by_user_id INTEGER,
    version INTEGER NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT pk_performance_reviews PRIMARY KEY (id),
    CONSTRAINT ck_performance_reviews_cancelled_has_reason CHECK (status <> 'CANCELLED' OR cancellation_reason IS NOT NULL),
    CONSTRAINT ck_performance_reviews_finalized_has_time CHECK (status <> 'FINALIZED' OR finalized_at IS NOT NULL),
    CONSTRAINT ck_performance_reviews_submitted_has_rating CHECK (status NOT IN ('MANAGER_SUBMITTED', 'FINALIZED') OR overall_rating IS NOT NULL),
    CONSTRAINT ck_performance_reviews_rating_range CHECK (overall_rating IS NULL OR (overall_rating >= 1 AND overall_rating <= 5)),
    CONSTRAINT ck_performance_reviews_version_positive CHECK (version >= 1),
    CONSTRAINT fk_performance_reviews_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_performance_reviews_cycle_id_performance_cycles FOREIGN KEY(cycle_id) REFERENCES performance_cycles (id) ON DELETE RESTRICT,
    CONSTRAINT fk_performance_reviews_employee_id_employees FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE RESTRICT,
    CONSTRAINT fk_performance_reviews_finalized_by_user_id_users FOREIGN KEY(finalized_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_performance_reviews_reviewer_user_id_users FOREIGN KEY(reviewer_user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_performance_reviews_updated_by_user_id_users FOREIGN KEY(updated_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT uq_performance_reviews_cycle_id UNIQUE (cycle_id, employee_id)
);

CREATE INDEX ix_performance_reviews_employee_status ON performance_reviews (employee_id, status);

CREATE INDEX ix_performance_reviews_reviewer_user_id ON performance_reviews (reviewer_user_id);

CREATE INDEX ix_performance_reviews_status ON performance_reviews (status);

CREATE TABLE performance_events (
    id INTEGER NOT NULL AUTO_INCREMENT,
    cycle_id INTEGER,
    goal_id INTEGER,
    review_id INTEGER,
    version INTEGER NOT NULL,
    event_type ENUM('CYCLE_CREATED','CYCLE_UPDATED','CYCLE_OPENED','CYCLE_REVIEWS_STARTED','CYCLE_COMPLETED','CYCLE_CANCELLED','GOAL_CREATED','GOAL_UPDATED','GOAL_PROGRESS_CHANGED','GOAL_STATUS_CHANGED','GOAL_REASSIGNED','REVIEW_CREATED','REVIEW_UPDATED','REVIEW_REASSIGNED','SELF_ASSESSMENT_SUBMITTED','MANAGER_REVIEW_SUBMITTED','REVIEW_FINALIZED','REVIEW_REOPENED','REVIEW_CANCELLED') NOT NULL,
    from_status VARCHAR(30),
    to_status VARCHAR(30),
    actor_user_id INTEGER,
    details JSON,
    created_at DATETIME NOT NULL,
    CONSTRAINT pk_performance_events PRIMARY KEY (id),
    CONSTRAINT ck_performance_events_single_subject CHECK ((CASE WHEN cycle_id IS NULL THEN 0 ELSE 1 END) + (CASE WHEN goal_id IS NULL THEN 0 ELSE 1 END) + (CASE WHEN review_id IS NULL THEN 0 ELSE 1 END) = 1),
    CONSTRAINT fk_performance_events_actor_user_id_users FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_performance_events_cycle_id_performance_cycles FOREIGN KEY(cycle_id) REFERENCES performance_cycles (id) ON DELETE RESTRICT,
    CONSTRAINT fk_performance_events_goal_id_performance_goals FOREIGN KEY(goal_id) REFERENCES performance_goals (id) ON DELETE RESTRICT,
    CONSTRAINT fk_performance_events_review_id_performance_reviews FOREIGN KEY(review_id) REFERENCES performance_reviews (id) ON DELETE RESTRICT,
    CONSTRAINT uq_performance_events_cycle_id UNIQUE (cycle_id, version),
    CONSTRAINT uq_performance_events_goal_id UNIQUE (goal_id, version),
    CONSTRAINT uq_performance_events_review_id UNIQUE (review_id, version)
);

UPDATE alembic_version SET version_num='b647981f350c' WHERE alembic_version.version_num = '0d5f1e6ae076';

-- Running upgrade b647981f350c -> a0bd1c34f49c

ALTER TABLE recommendations MODIFY category ENUM('SUPPORT_CHECK_IN','WORKLOAD_REVIEW','CAREER_DEVELOPMENT','DEPARTMENT_RETENTION_REVIEW','WORKFORCE_TREND_REVIEW','DATA_QUALITY','FOLLOW_UP_REVIEW','GOAL_SUPPORT','DATA_REFRESH') NOT NULL;

ALTER TABLE recommendations MODIFY priority ENUM('LOW','MEDIUM','HIGH','CRITICAL') NOT NULL;

ALTER TABLE recommendations MODIFY status ENUM('AVAILABLE','ACCEPTED','DISMISSED','COMPLETED','SUPERSEDED') NOT NULL;

ALTER TABLE recommendations ADD COLUMN task_id INTEGER;

ALTER TABLE recommendations ADD COLUMN rank_score SMALLINT;

ALTER TABLE recommendations ADD COLUMN priority_reasons JSON;

ALTER TABLE recommendations ADD COLUMN evidence_fingerprint VARCHAR(64);

ALTER TABLE recommendations ADD COLUMN superseded_by_id INTEGER;

CREATE INDEX ix_recommendations_task_id ON recommendations (task_id);

ALTER TABLE recommendations ADD CONSTRAINT fk_recommendations_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE RESTRICT;

ALTER TABLE recommendations ADD CONSTRAINT fk_recommendations_superseded_by_id_recommendations FOREIGN KEY(superseded_by_id) REFERENCES recommendations (id) ON DELETE RESTRICT;

ALTER TABLE recommendations ADD CONSTRAINT ck_recommendations_rank_score_range CHECK (rank_score IS NULL OR (rank_score >= 0 AND rank_score <= 100));

UPDATE alembic_version SET version_num='a0bd1c34f49c' WHERE alembic_version.version_num = 'b647981f350c';

-- Running upgrade a0bd1c34f49c -> c4e7a91d3f20

CREATE TABLE revoked_tokens (
    jti VARCHAR(64) NOT NULL,
    user_id INTEGER NOT NULL,
    expires_at DATETIME NOT NULL,
    revoked_at DATETIME NOT NULL,
    CONSTRAINT pk_revoked_tokens PRIMARY KEY (jti),
    CONSTRAINT fk_revoked_tokens_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_revoked_tokens_user_id ON revoked_tokens (user_id);

CREATE INDEX ix_revoked_tokens_expires_at ON revoked_tokens (expires_at);

ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0;

UPDATE alembic_version SET version_num='c4e7a91d3f20' WHERE alembic_version.version_num = 'a0bd1c34f49c';
