# Attrition-risk model (`ml/attrition`)

Decision support for HR. It is **not** a system that decides anyone's future, and it must never be the
sole basis for an employment decision.

> **Status:** the training pipeline is implemented and tested on **synthetic** data only. **No real model
> has been trained or validated.** No metric produced by the tests describes real-world performance.

## Layout

| Module | Responsibility |
|--------|----------------|
| `features.py` | Versioned feature schema (`attrition-features-v1`), validation, forbidden columns |
| `training.py` | Reproducible training and held-out evaluation |
| `artifacts.py` | Saving and integrity-checked loading of model artifacts |
| `predictor.py` | Probability → score (0–100) → category, plus explanations |

There is no FastAPI and no database code here. `app/services/attrition.py` extracts features from platform
data and manages the model registry; `app/api/attrition.py` exposes the endpoints.

## Feature schema `attrition-features-v1`

| Feature | Type | Missing allowed | Source / definition | Rationale |
|---------|------|-----------------|---------------------|-----------|
| `tenure_months` | 0–600 | no | Whole months since `date_of_joining` | Risk varies with tenure |
| `late_rate_90d` | 0–1 | yes | LATE ÷ (PRESENT + LATE + HALF_DAY) records, last 90 days | Punctuality change |
| `avg_overtime_minutes_90d` | 0–1440 | yes | Mean `overtime_minutes` of checked-out days, last 90 days | Sustained overtime / burnout |
| `recorded_absence_days_90d` | 0–90 | no | Records explicitly marked ABSENT, last 90 days | Explicit absence only |
| `attendance_days_recorded_90d` | 0–90 | no | Attendance records of any status, last 90 days | Data coverage, so sparse data is not mistaken for behaviour |
| `department` | category | yes | Department id | Team-level conditions; job-related |

**Missing data is never treated as absence.** Days without a record are not absences. Missing numeric inputs
are imputed with the training median **plus a missing-indicator** (when the training data had gaps), and
missing inputs are listed in each prediction and excluded from its explanation.

**Deliberately excluded**

- Identifiers: name, email, employee code, phone, user id. These are rejected in training data.
- Protected or sensitive characteristics and proxies: age, gender, marital status, religion, nationality,
  ethnicity, disability, health. **Leave patterns are excluded in v1** because leave types such as maternity
  or sick leave are proxies for pregnancy and health.
- Salary and payroll (sensitive; needs HR/legal review first).
- Employment status and anything recorded after an exit decision (would leak the outcome).

**Not available yet (future integrations, not invented):** recent performance-review score (no review
module) and months since last promotion (no promotion history).

## Training dataset (via the dataset registry)

Training data is uploaded through `POST /api/datasets` and must pass validation
(`ml/attrition/dataset_validation.py`, which uses the same schema as `features.py`) before it can be used.

- **Columns:** exactly the six feature columns plus `attrited` (1 = left, 0 = stayed). This is an
  **allowlist**: any other column is fatal. Identifiers and sensitive characteristics are reported as
  prohibited, including names that only *contain* fragments such as `name`, `email`, `salary` or `birth`.
- **Snapshot timing:** each row must describe an employee **as of a date before the outcome**, to avoid leakage.
- **Fatal errors** (status REJECTED): any invalid cell, a missing required value, a target other than `0`/`1`,
  fewer than `ML_MIN_TRAINING_ROWS` (50) distinct valid rows, a missing class, or fewer than
  `ML_MIN_CLASS_COUNT` (10) distinct rows in a class.
  - Invalid cells are non-numeric values, out-of-range numbers, or malformed categories.
- **Warnings** (still eligible): exact duplicate rows, more than 50% missing values in a nullable feature,
  and department values not matching current departments (these are treated as unknown categories).
- **Duplicates** are reported, never removed from the stored file. Training uses distinct rows only and
  records how many duplicates it dropped.

## Training and evaluation

- Pipeline: median imputation with missing-indicators and standard scaling (numeric); constant
  `UNKNOWN` imputation and one-hot encoding (department, unknown categories ignored); then
  **logistic regression** (interpretable coefficients).
- **Class imbalance:** `class_weight="balanced"` (re-weighting; no resampling).
- **Evaluation:** a stratified 75/25 hold-out split with a fixed `ML_RANDOM_STATE`. All preprocessing is fitted
  on the training split only. Metrics on the hold-out split: precision, recall and F1 at a 0.5 decision
  threshold, ROC-AUC (only when both classes are present), and the confusion matrix.
- Recorded with every model: the dataset id, version and SHA-256 (also `dataset_version_id` in the
  registry), dataset fingerprint (SHA-256 of the canonical data), feature-schema version,
  model version (`lr-<timestamp>-<fingerprint prefix>`), training time, row counts, class balance,
  configuration, scikit-learn version, and `production_validated: false`. **This flag is never set
  automatically.**
- Same data plus same configuration gives the same model and metrics.

## Activation and artifacts

- A model can become ACTIVE only if its held-out ROC-AUC is at least `ML_ACTIVATION_MIN_ROC_AUC` (0.65).
  Otherwise it is REJECTED. **Training never activates a model**: a passing model is a CANDIDATE until an
  admin activates it (`POST /models/{model_version}/activate`), which retires the previous model. A working
  model is never replaced by one that
  failed the gate.
- Artifacts are stored at `ML_ARTIFACT_DIR/<model_version>/` as `model.joblib` and `metadata.json`.
  Existing versions are never overwritten.
- joblib uses pickle, so loading is restricted:
  - version names must match a strict pattern and resolve inside the directory;
  - the file's SHA-256 must equal the value stored in the database **before** unpickling;
  - the metadata must match the artifact format and feature schema;
  - the object must be the expected Pipeline type with the expected inputs.

  Users can never upload or name a model file.
- Nothing is trained or loaded at application startup. `backend/ml/models/*` and `backend/ml/datasets/` are
  git-ignored.

## Scores and explanations

- `risk_score` = estimated probability × 100, rounded to 0.01. **It is not a confidence score** and not proof
  that anyone will leave. Categories: LOW below `ML_RISK_MEDIUM_THRESHOLD` (40), MEDIUM below
  `ML_RISK_HIGH_THRESHOLD` (70), otherwise HIGH.
- Explanations list up to three inputs with the largest contribution to the model's log-odds for this
  employee ("raises" or "lowers" the estimate relative to the training data). These are **associations, not
  causes**.
- Aggregate evaluation metrics do not guarantee accuracy for any individual.
