# AI prediction APIs: attrition-risk estimation

This guide covers the attrition-risk endpoints of WorkForce AI Pro: what each one does, who may call it, and what it returns. Every endpoint, role and field below comes from the application code. The examples in [`examples/`](examples) are **live responses** captured from the running application in the disposable demo environment ([`../tools/demo_server.py`](../tools/demo_server.py)), with synthetic people and data only. Access tokens are redacted.

> **The attrition model is not production-validated.** The repository contains no model trained or evaluated on real data. In the demo, a model was trained on the repository's **synthetic** data generator (`tests/synthetic_attrition.py`), which plants an artificial signal. Its evaluation numbers (for example ROC-AUC 0.92 on a 150-row held-out split) describe that generated data only and say **nothing** about real-world accuracy. The application itself records `"production_validated": false` for the model. Risk scores are statistical review aids, never a basis for employment decisions.

## Endpoint inventory

All paths are under `/api`. Roles come from each route's `require_roles` dependency.

| Method | Path | Purpose | Roles | Request | Success response |
|---|---|---|---|---|---|
| POST | `/ai/attrition/predict/{employee_id}` | Store one risk estimate for a current employee | ADMIN, HR, MANAGER (own department; never oneself) | none | 201 `PredictionRead` ([example](examples/predict_one.json)) |
| POST | `/ai/attrition/predict-batch` | Estimates for up to 100 employees; unknown or out-of-scope ids are reported as "Not found" | ADMIN, HR, MANAGER | `{"employee_ids": [..]}` (unique, 1–100) | 200 `BatchPredictResult` ([example](examples/predict_batch_with_unknown_id.json)) |
| GET | `/ai/attrition/history/{employee_id}` | Stored predictions for one employee, newest first, paginated | ADMIN, HR, MANAGER (scoped) | `page`, `page_size` | 200 `Page[PredictionRead]` ([example](examples/prediction_history.json)) |
| GET | `/ai/attrition/summary` | Counts of latest estimates by category (aggregate) | ADMIN, HR | `department_id` (optional) | 200 ([example](examples/risk_summary.json)) |
| GET | `/ai/attrition/model-status` | Whether a usable model is active; registry, feature schema, thresholds, limitations | ADMIN, HR | none | 200 `ModelStatus` ([example](examples/model_status.json)) |
| POST | `/ai/attrition/train` | Train a **candidate** model from a VALIDATED dataset version (never activates it) | ADMIN | `{"dataset_id": n, "version": n?}` | 201 `TrainResult` (model, dataset, `gate_failures`) |
| POST | `/ai/attrition/models/{model_version}/activate` | Activate a trained model that passed the quality gate; retires the previous one | ADMIN | none | 200 `ModelVersionRead` |
| POST | `/datasets` | Upload a CSV as a new dataset (multipart `file`, `display_name`) | ADMIN | multipart | 201 `DatasetRead` |
| POST | `/datasets/{id}/versions` | Add a new immutable version | ADMIN | multipart `file` | 201 |
| POST | `/datasets/{id}/validate` | Validate a version (`?version=n`, default latest) | ADMIN | query | 200 `ValidationReportRead` |
| GET | `/datasets`, `/datasets/{id}`, `/datasets/{id}/versions`, `/datasets/{id}/validation` | Metadata and validation reports (no cell values) | ADMIN, HR | filters / pagination | 200 ([examples](examples/datasets.json), [validation](examples/dataset_validation_report.json)) |
| POST | `/datasets/{id}/archive` · GET `/datasets/{id}/download` | Archive; download a version (audited) | ADMIN | — | 200 |
| POST | `/risk-monitoring/run` | Compare stored predictions and raise or refresh review alerts (no model call) | ADMIN, HR | `{"limit": n, "after_employee_id": n}` | 200 `RunSummary` ([example](examples/monitoring_run.json)) |
| GET | `/risk-monitoring/alerts`, `/risk-monitoring/alerts/{id}` | Review alerts (scoped; never about oneself) | ADMIN, HR, MANAGER | filters | 200 ([example](examples/risk_alerts.json)) |

The full list of 172 operations is in [`../07_API_Documentation/endpoint_inventory.md`](../07_API_Documentation/endpoint_inventory.md).

## Model lifecycle (as implemented)

1. **Upload** (ADMIN). `POST /datasets` with a CSV, which is stored under a server-generated name.
   - Limits: 10 MB, 200,000 rows, 50 columns.
   - Identifier and sensitive columns are refused at validation.
2. **Validate** (ADMIN). `POST /datasets/{id}/validate`.
   - Checks the approved feature allowlist, types, ranges and duplicates.
   - The report holds counts and issue codes only, never cell values ([example](examples/dataset_validation_report.json)).
   - A validated version cannot be revalidated against the same schema: 409 ([example](examples/revalidate_validated_409.json)).
3. **Train** (ADMIN). `POST /ai/attrition/train`.
   - Trains a logistic-regression candidate and evaluates it on a held-out split.
   - Applies the activation gate (`ML_ACTIVATION_MIN_ROC_AUC`, default 0.65).
   - **Training never activates the model.** HR gets 403 ([example](examples/train_forbidden_for_hr.json)).
4. **Activate** (ADMIN). An explicit, separate call. Models that fail the gate cannot be activated.
5. **Predict** (ADMIN, HR, or MANAGER for their own department).
   - Features are computed from the last 90 days of attendance plus tenure and department.
   - The response includes a 0–100 `risk_score`, a category (LOW < 40 ≤ MEDIUM < 70 ≤ HIGH by default), the top contributing factors, any missing inputs and the limitations.
   - Employees cannot call it: 403 ([example](examples/predict_forbidden_for_employee.json)).
6. **Monitor** (ADMIN, HR). `POST /risk-monitoring/run` compares stored predictions, records events and raises review alerts. Alerts have review deadlines and escalation (`POST /risk-monitoring/escalations/evaluate`).

## Model-unavailable responses

When no usable model exists, prediction endpoints return **503**, write an audit row (`ai.attrition.prediction_unavailable`), and never invent a score. The body looks like this:

```json
{"detail": {"code": "no_active_model", "message": "No attrition model has been trained and activated",
            "hint": "An administrator must train and activate a model on a validated dataset."}}
```

| `code` | Meaning |
|---|---|
| `no_active_model` | Nothing has been trained and activated (the state of a fresh installation) |
| `schema_mismatch` | The active model was trained on an older feature schema |
| `artifact_error` | The model files are missing or fail their SHA-256 check (tampering or corruption) |

This shape comes from `app/api/attrition.py` (`_load_model_or_503`) and is verified by the existing tests `test_no_model_returns_503_and_never_invents_scores` and `test_tampered_artifact_makes_model_unavailable`.

It was **not** captured live here, because the demo environment activated its synthetic model during seeding. The demo did record `available: false` from `GET /ai/attrition/model-status` before training. In the UI, the Datasets page then shows "No usable model".

## Privacy and safety rules

- **Scope:** nobody sees or generates estimates about themselves. Managers are limited to their own department; out-of-scope employees return 404.
- **Exposure:** risk scores are never searchable, never shown to employees, and appear only as aggregates in reports. The UI's risk pages do not display the numeric score.
- **Inputs:** features exclude protected characteristics and identifiers, and the dataset validator refuses such columns.

## Source

A copy of the relevant modules is in [`source/`](source):
- **Routers:** `app/api/attrition.py`, `datasets.py`, `risk_monitoring.py`.
- **Services:** `app/services/attrition.py`, `datasets.py`, `dataset_storage.py`, `risk_monitoring.py`, `alert_sla.py`.
- **ML package:** `ml/attrition/` (`features.py`, `training.py`, `predictor.py`, `artifacts.py`, `dataset_validation.py`).
- **Tests:** schemas, models and the related tests.

The complete backend is in [`../01_Backend_Source_Code/`](../01_Backend_Source_Code).

## Required before any production use

All of these are outstanding:
- An approved, lawfully obtained training dataset.
- Held-out evaluation with calibration.
- A fairness and bias review with legal and HR sign-off.
- Drift monitoring and a retraining policy.
- A documented intended use: review support only.

See [`../09_Test_Results/TEST_RESULTS.md`](../09_Test_Results/TEST_RESULTS.md) for the AI tests that ran.
