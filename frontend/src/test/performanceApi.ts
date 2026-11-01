/**
 * The performance page's API, answered from fixtures.
 *
 * The validation curve is the simulator's synthetic sweep (1,250 customers,
 * 310 churners, the 101-point grid), with the stayers each cut leaves alone;
 * three points are its own on purpose: at 0.36 the stayers left alone are not
 * customers less churners less false positives, at 0.97 three are flagged and
 * none churned (precision 0, defined), and at 0 the precision is not
 * churners over customers. The measured block's figures differ from the
 * curve's at the same cut (222 of 385 flagged here, 296 of 512 on the curve),
 * and no rate in it, or in the record, is its counts' quotient at the three
 * digits shown: a page that reads one where it should read the other, or
 * works a figure out instead of reading it, shows another number.
 *
 * Nothing here is a real figure: the recorded test block is invented, so no
 * test-partition number is ever typed into the front end, even in a test.
 */
import type { Performance } from '../api/queries';
import { answer } from './server';
import { CHURNERS, CUSTOMERS, sweepFor } from './simulatorApi';

type Curve = NonNullable<Performance['validation']>;
type Measured = NonNullable<Performance['validation_evaluation']>;
type Recorded = NonNullable<Performance['test']>;

/** The artifact ``modelInfo()`` serves, so the two agree unless a test says otherwise. */
export const ARTIFACT = 'a'.repeat(64);

/** Points whose figures no formula over the others gives (see the header). */
const OWN: Record<number, Partial<Curve['points'][number]>> = {
  0: { precision: 0.2601 },
  36: { true_negatives: 701 },
  97: {
    flagged: 3,
    true_positives: 0,
    false_positives: 3,
    false_negatives: CHURNERS,
    precision: 0,
    recall: 0,
    f1: 0,
    true_negatives: CUSTOMERS - CHURNERS - 3,
  },
};

export function curve(): Curve {
  return {
    customers: CUSTOMERS,
    churners: CHURNERS,
    points: sweepFor().points.map((point, i) => ({
      ...point,
      true_negatives: CUSTOMERS - CHURNERS - point.false_positives,
      ...OWN[i],
    })),
  };
}

export function measured(overrides: Partial<Measured> = {}): Measured {
  return {
    metrics: {
      threshold: 0.36,
      n: CUSTOMERS,
      positives: CHURNERS,
      tn: 777,
      fp: 163,
      fn: 88,
      tp: 222,
      accuracy: 0.8123,
      precision: 0.6012,
      recall: 0.7433,
      f1: 0.6544,
      roc_auc: 0.8412,
      average_precision: 0.6537,
      brier: 0.1366,
    },
    churn_rate: 0.2711,
    no_skill_accuracy: 0.7333,
    ece: 0.0311,
    reliability: [
      { low: 0, high: 0.1, customers: 412, mean_predicted: 0.0432, churned: 21, rate: 0.051 },
      { low: 0.1, high: 0.2, customers: 233, mean_predicted: 0.1488, churned: 37, rate: 0.1588 },
      { low: 0.3, high: 0.4, customers: 198, mean_predicted: 0.3471, churned: 71, rate: 0.3586 },
      { low: 0.6, high: 0.7, customers: 160, mean_predicted: 0.6522, churned: 101, rate: 0.6313 },
      { low: 0.8, high: 0.9, customers: 14, mean_predicted: 0.8233, churned: null, rate: null },
    ],
    segments: [
      segment('Contract', 'Month-to-month', 690, 281, 352, 0.5533, 0.7011),
      segment('Contract', 'One year', 268, 25, 33, 0.3011, 0.4022),
      {
        ...segment('Contract', 'Two year', 292, 4, 0, null, 0),
        precision_low: null,
        precision_high: null,
        unscanned: true,
      },
      // Nobody flagged and nobody churned: not "not scanned", and both rates undefined.
      {
        ...segment('InternetService', 'No', 290, 0, 0, null, null),
        precision_low: null,
        precision_high: null,
      },
      // One bound missing: no interval; no ranking within it.
      {
        ...segment('SeniorCitizen', '0', 985, 220, 230, 0.5512, 0.6633),
        precision_high: null,
        roc_auc: null,
      },
      segment('SeniorCitizen', '1', 205, 87, 120, 0.5911, 0.8044),
      {
        ...segment('gender', 'Female', 12, null, 5, null, null),
        churn_rate: null,
        flagged_rate: null,
        roc_auc: null,
        recall_low: null,
        recall_high: null,
        precision_low: null,
        precision_high: null,
        unscanned: null,
      },
    ],
    ...overrides,
  };
}

/** A segment row whose figures are its own: no interval or rate is worked out from another. */
function segment(
  dimension: string,
  name: string,
  customers: number,
  churners: number | null,
  flagged: number,
  precision: number | null,
  recall: number | null,
): Measured['segments'][number] {
  return {
    dimension,
    segment: name,
    customers,
    churners,
    churn_rate: churners === null ? null : 0.4011,
    flagged,
    flagged_rate: 0.5011,
    precision,
    precision_low: precision === null ? null : 0.4811,
    precision_high: precision === null ? null : 0.6311,
    recall,
    recall_low: recall === null ? null : 0.0122,
    recall_high: recall === null ? null : 0.8911,
    roc_auc: 0.7123,
    unscanned: false,
  };
}

export function recorded(overrides: Partial<Recorded> = {}): Recorded {
  return {
    evaluated_at: '2026-10-21T00:00:00+00:00',
    metrics: {
      threshold: 0.4,
      n: 1400,
      positives: 350,
      tn: 800,
      fp: 250,
      fn: 100,
      tp: 250,
      accuracy: 0.7511,
      precision: 0.4987,
      recall: 0.7123,
      f1: 0.5866,
      roc_auc: 0.8301,
      average_precision: 0.6111,
      brier: 0.1402,
    },
    churn_rate: 0.2611,
    ece: 0.0299,
    no_skill_accuracy: 0.7501,
    test_optimal_threshold: 0.29,
    segments: [
      {
        dimension: 'Contract',
        segment: 'Month-to-month',
        customers: 770,
        churners: 300,
        churn_rate: 0.3896,
        flagged: 450,
        flagged_rate: 0.5844,
        precision: 0.5011,
        recall: 0.7511,
        unscanned: false,
      },
      {
        dimension: 'Contract',
        segment: 'Two year',
        customers: 330,
        churners: 9,
        churn_rate: 0.0273,
        flagged: 0,
        flagged_rate: 0,
        precision: null,
        recall: 0,
        unscanned: true,
      },
    ],
    ...overrides,
  };
}

export function performance(overrides: Partial<Performance> = {}): Performance {
  return {
    display_name: 'Logistic Regression',
    model_sha256: ARTIFACT,
    threshold: 0.36,
    selection: {
      rule: 'one standard error on the cross-validated score',
      metric: 'average_precision',
      folds: 5,
      leader: 'random_forest',
      leader_name: 'Random Forest',
      standard_error: 0.0071,
      cutoff: 0.6633,
      eligible: ['random_forest', 'hist_gradient_boosting', 'logistic_regression'],
    },
    candidates: [
      {
        model: 'Random Forest',
        cv_pr_auc: 0.6704,
        cv_std: 0.0159,
        cv_se: 0.0071,
        within_one_se: true,
        val_pr_auc: 0.6321,
        val_roc_auc: 0.8355,
        selected: false,
      },
      {
        model: 'Hist Gradient Boosting',
        cv_pr_auc: 0.6688,
        cv_std: 0.0141,
        cv_se: 0.0063,
        within_one_se: true,
        val_pr_auc: 0.6402,
        val_roc_auc: 0.8344,
        selected: false,
      },
      {
        model: 'Logistic Regression',
        cv_pr_auc: 0.6652,
        cv_std: 0.0233,
        cv_se: 0.0104,
        within_one_se: true,
        val_pr_auc: 0.6433,
        val_roc_auc: 0.8366,
        selected: true,
      },
      {
        model: 'Baseline (predicts the prior)',
        cv_pr_auc: 0.2611,
        cv_std: 0.0004,
        cv_se: 0.0002,
        within_one_se: false,
        val_pr_auc: 0.2622,
        val_roc_auc: 0.5,
        selected: false,
      },
    ],
    calibration: {
      method: 'isotonic',
      epsilon: 0.000118,
      uncalibrated: { precision: 0.5011, recall: 0.7777, brier: 0.1666 },
      calibrated: { precision: 0.6611, recall: 0.5222, brier: 0.1388 },
      uncalibrated_ece: 0.1444,
      calibrated_ece: 0.0277,
    },
    bands: [],
    validation: curve(),
    validation_evaluation: measured(),
    test: null,
    test_reason:
      'No test evaluation is recorded for this model. It is written by `make evaluate CONFIRM=yes`.',
    withheld_reason: null,
    ...overrides,
  };
}

export function performanceApi(body: Performance = performance()) {
  return answer('get', '/api/performance', body);
}
