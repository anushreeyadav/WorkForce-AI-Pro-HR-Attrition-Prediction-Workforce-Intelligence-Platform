# Attrition model: validation status and protocol (Sub-prompt 32)

**Status: production model validation is BLOCKED.** No authorized, representative, lawfully obtained evaluation dataset exists, and there are no documented acceptance criteria. Every model trained in this project was trained on synthetic data, and every metric below describes synthetic data only. No real-world accuracy, calibration or fairness is claimed. No production model has been activated.

## What the pipeline guarantees (verified by tests on synthetic data)

| Control | Evidence |
|---|---|
| Only registered, VALIDATED, training-eligible dataset versions can be trained on. Eligibility is decided server-side, and the stored file is re-checked by checksum and re-validated against the current feature schema before training | `test_attrition_api.py::test_training_refuses_ineligible_datasets`, `test_training_refuses_tampered_dataset_file`, `test_train_accepts_only_registered_dataset_ids` |
| An allowlisted feature schema: identifiers, protected characteristics and their proxies (leave, salary) are refused | `test_dataset_validation.py`, `ml/README.md` |
| Train/evaluation separation: a stratified hold-out split, with all preprocessing fitted on the training split only | `test_attrition_ml.py::test_preprocessing_fitted_on_training_split_only` |
| Reproducibility: the same data and configuration give the same model and metrics | `test_attrition_ml.py::test_training_is_reproducible` |
| Dataset traceability: every model records the dataset id, version, SHA-256 and a fingerprint | `test_training_creates_candidate_with_dataset_traceability` |
| Training never activates. A model below `ML_ACTIVATION_MIN_ROC_AUC` is REJECTED and cannot be activated. Only an ADMIN can activate | `test_model_failing_gate_is_rejected_and_cannot_be_activated`, `test_only_admin_trains_or_activates` |
| **New: activation re-checks the candidate first.** The artifact must pass the integrity check (checksum before unpickling, metadata, pipeline type), the feature schema must be current, and the stored ROC-AUC must meet the **current** minimum. Otherwise the request gets 409, the working model stays ACTIVE, and an `ai.attrition.activation_refused` audit row is written | `test_activation_verifies_the_candidate_artifact_first` (tampered and deleted artifact), `test_activation_rechecks_the_gate_under_current_settings`, `test_rejected_models_are_refused_without_audit_noise` |
| Safe behavior with no usable model: 503 with `no_active_model`, `schema_mismatch` or `artifact_error`; no score is invented | `test_no_model_returns_503_and_never_invents_scores`, `test_tampered_artifact_makes_model_unavailable`; **browser:** `qa/browser_tests.py::t09` shows "No usable model" on a fresh server |
| **New (MySQL bug fixed):** feature values are plain JSON numbers. On MySQL, `SUM()` returns `Decimal`, and every prediction for an employee with attendance failed with a 500 | `test_production_hardening.py::test_features_are_plain_json_types_when_the_database_returns_decimals`; the full suite on MySQL |

## Evaluation report (extended in this phase)

Each trained model's `evaluation.metrics` now contains:
- `precision`, `recall`, `f1` and the confusion matrix at the 0.5 reference threshold, and `roc_auc`;
- **`at_operating_thresholds`:** flagged rate, precision and recall at the thresholds the product actually uses (`ML_RISK_MEDIUM_THRESHOLD`/100 and `ML_RISK_HIGH_THRESHOLD`/100, i.e. 0.40 and 0.70 by default). These are reported, never tuned automatically;
- **`brier_score`:** lower is better; 0.25 is what a constant 0.5 estimate scores;
- **`calibration`:** a five-bin reliability table (mean estimate vs. observed rate, with counts);
- `evaluation_rows`, and a `scope` statement: "Held-out split of the training dataset. Describes that dataset only; not evidence of real-world accuracy, calibration or fairness."

**Demonstration only (synthetic data, generator `backend/tests/synthetic_attrition.py`, 400 rows, 100 held out).** This is the QA server's model `lr-20261028041115-3e7c504b`:
- **Overall:** ROC-AUC 0.873 and Brier score 0.149.
- **At 0.40:** 57% flagged, precision 0.84, recall 0.84.
- **At 0.70:** 36% flagged, precision 0.86, recall 0.54.
- **Calibration**, as mean estimate → observed rate:

  | Bin | Mean estimate | Observed rate | Rows |
  |---|---|---|---|
  | 0.0–0.2 | 0.07 | 0.19 | 27 |
  | 0.2–0.4 | 0.31 | 0.25 | 16 |
  | 0.4–0.6 | 0.48 | 0.60 | 10 |
  | 0.6–0.8 | 0.70 | 0.85 | 20 |
  | 0.8–1.0 | 0.92 | 0.93 | 27 |

These numbers only show that the report works. The generator plants the signal, so they say nothing about real employees. With `class_weight="balanced"`, probabilities are not expected to be calibrated in general.

## Protocol required before any production activation (owner: HR, legal and data-protection leads)

1. **Data:** a lawfully obtained, documented historical dataset under the `attrition-features-v1` schema, with each row describing an employee *before* the outcome (no leakage). Data-protection approval is needed, plus a written retention period.
2. **Acceptance criteria, agreed and signed before evaluation:**
   - minimum ROC-AUC (`ML_ACTIVATION_MIN_ROC_AUC`);
   - minimum precision at the HIGH threshold;
   - maximum flagged rate;
   - maximum calibration error;
   - subgroup tolerances.
3. **Evaluation:**
   - **Time-based hold-out:** train on earlier periods and test on the latest. The built-in random split is a first check only.
   - **Repeated evaluation:** cross-validation or bootstrap confidence intervals, not a single split.
   - **Thresholds:** choose them from the precision, recall and flagged-rate trade-off and the review capacity of HR, then set them in configuration.
   - **Calibration:** check the reliability table and the Brier score. If calibrated probabilities are needed, add a calibration step (for example isotonic regression on a separate split) as a reviewed code change.
4. **Subgroup and fairness analysis:**
   - Compare error rates, flagged rates and calibration across relevant groups, by department and, where lawful and available *outside* the model's features, protected characteristics.
   - Protected attributes are deliberately absent from the platform, so this analysis needs a separate, access-controlled join performed by the data-protection owner.
   - **Not implemented in code.**
5. **Human oversight:**
   - scores are review aids only (UI and API wording already says so); no automated decisions;
   - reviewers are trained;
   - alert outcomes are recorded through the existing acknowledge, resolve and dismiss workflow.
6. **Activation:** by an ADMIN, recording the reference to the signed evaluation. The activation-time checks above still apply. Then set `production_validated` only through a reviewed change (no API sets it).
7. **Monitoring:**
   - prediction volumes by category (`/api/ai/attrition/model-status`);
   - drift of the feature distributions;
   - an outcome-based re-evaluation on a fixed schedule;
   - retraining only through the same protocol.

## Rollback and recovery

- **Roll back to an earlier model:** a RETIRED model cannot be re-activated directly. Retrain from the same registered dataset version: training is deterministic, so the result is the same model under a new version name, as a CANDIDATE. Then activate it; the activation-time checks run again.
- **Missing or tampered artifact:**
  - prediction returns 503 `artifact_error`;
  - restore `ML_ARTIFACT_DIR/<version>/` from backup (artifacts are verified by the SHA-256 stored in the database), or retrain;
  - activation of a damaged candidate is refused.
- **Withdraw the model entirely:** there is no "deactivate" endpoint. An operator sets the ACTIVE row to RETIRED with a reviewed database change; predictions then return 503 `no_active_model`. *(A dedicated, audited deactivate endpoint is a recommended follow-up.)*
