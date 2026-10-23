/**
 * Complete response bodies for component tests, shaped exactly like the API's:
 * each builder's return type is the generated schema type, so a field the
 * server adds or renames fails the type check here first. Identifiers and
 * digests are synthetic. A test overrides only the fields it is about.
 */
import type { Health, ModelInfo } from '../api/queries';

export function health(overrides: Partial<Health> = {}): Health {
  return { status: 'ok', model_loaded: true, detail: null, ...overrides };
}

export function modelInfo(overrides: Partial<ModelInfo> = {}): ModelInfo {
  return {
    model_key: 'logistic_regression',
    display_name: 'Logistic Regression',
    trained_at: '2026-10-13T20:13:55+00:00',
    threshold: 0.36,
    threshold_source:
      "highest threshold whose simulated net benefit on the validation partition is within one retention offer's cost of the best",
    calibrated: true,
    calibration_method: 'isotonic',
    feature_columns: [
      'SeniorCitizen',
      'Partner',
      'Dependents',
      'tenure',
      'PhoneService',
      'MultipleLines',
      'InternetService',
      'OnlineSecurity',
      'OnlineBackup',
      'DeviceProtection',
      'TechSupport',
      'StreamingTV',
      'StreamingMovies',
      'Contract',
      'PaperlessBilling',
      'PaymentMethod',
      'MonthlyCharges',
      'TotalCharges',
    ],
    dropped_columns: ['customerID', 'gender'],
    n_train: 4225,
    n_validation: 1409,
    validation_metrics: { threshold: 0.5, roc_auc: 0.8375 },
    test_metrics: null,
    sklearn_version: '1.9.1',
    limitations: ['Predicts association with past churn; it is not a causal model.'],
    model_sha256: 'a'.repeat(64),
    probability_epsilon: 0.000118,
    risk_bands: [
      { name: 'Low', min: 0, max: 0.25 },
      { name: 'Moderate', min: 0.25, max: 0.5 },
      { name: 'High', min: 0.5, max: 0.75 },
      { name: 'Critical', min: 0.75, max: 1.01 },
    ],
    business_defaults: {
      currency: 'USD',
      retention_offer_cost: 50,
      offer_success_rate: 0.3,
      expected_horizon_months: 12,
      gross_margin: 0.65,
    },
    limits: { max_batch_rows: 10000, max_upload_bytes: 5242880, max_upload_columns: 1000 },
    provenance: {
      verified: true,
      data_sha256: 'b'.repeat(64),
      split_digest: 'c'.repeat(64),
      withheld_reason: null,
    },
    test_evaluation: null,
    ...overrides,
  };
}
