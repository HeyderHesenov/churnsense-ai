/**
 * The overview page's responses, complete and typed by the schema. The
 * figures are small round numbers chosen so a test can state the expected
 * text by hand; identifiers are synthetic.
 */
import type { CustomerPage, CustomerRow, ExplainResponse, Overview } from '../api/queries';
import type { Schemas } from '../api/client';

type Cell = Schemas['ReportedCell'] | Schemas['SuppressedCell'];

/** A reported level; its interval is a stand-in a test never reads. */
function reported(row: string, n: number, churned: number): Cell {
  const rate = churned / n;
  return {
    row,
    column: null,
    n,
    churned,
    rate,
    lift: null,
    rate_low: Math.max(0, rate - 0.02),
    rate_high: Math.min(1, rate + 0.02),
    suppressed: false,
  };
}

/** 101 thresholds, 0.00 to 1.00, with net benefit peaking at 0.30. */
function curve(): Schemas['CurvePoint'][] {
  return Array.from({ length: 101 }, (_, i) => {
    const threshold = i / 100;
    return { threshold, net_benefit: 30_000 - 100_000 * (threshold - 0.3) ** 2 };
  });
}

const BANDS = [
  { name: 'Low', min: 0, max: 0.25 },
  { name: 'Moderate', min: 0.25, max: 0.5 },
  { name: 'High', min: 0.5, max: 0.75 },
  { name: 'Critical', min: 0.75, max: 1.01 },
] as const;

export function overview(overrides: Partial<Overview> = {}): Overview {
  return {
    customers: 7000,
    monthly_charges: 450_000,
    threshold: 0.36,
    observed: { customers: 5600, churned: 1400, rate: 0.25 },
    current_monthly_charges: 320_000,
    unknown_customers: 1400,
    unknown_monthly_charges: 80_000,
    high_or_critical: {
      customers: 1500,
      monthly_charges: 120_000,
      share: 1500 / 7000,
      in_queue: 1200,
      in_queue_active: 900,
      in_queue_unknown: 300,
      in_queue_share: 1200 / 7000,
      in_queue_monthly: 96_000,
    },
    flagged: {
      customers: 2300,
      monthly_charges: 180_000,
      share: 2300 / 7000,
      in_queue: 1900,
      in_queue_active: 1500,
      in_queue_unknown: 400,
      in_queue_share: 1900 / 7000,
      in_queue_monthly: 150_000,
    },
    bands: [
      {
        ...BANDS[0],
        customers: 4000,
        share: 4000 / 7000,
        monthly_charges: 220_000,
        monthly_share: 220 / 450,
        flagged: 0,
        flagged_not_churned: 0,
        observed: { customers: 3200, churned: 256, rate: 0.08 },
      },
      {
        ...BANDS[1],
        customers: 1500,
        share: 1500 / 7000,
        monthly_charges: 110_000,
        monthly_share: 110 / 450,
        flagged: 800,
        flagged_not_churned: 700,
        observed: { customers: 1200, churned: 420, rate: 0.35 },
      },
      {
        ...BANDS[2],
        customers: 1100,
        share: 1100 / 7000,
        monthly_charges: 90_000,
        monthly_share: 90 / 450,
        flagged: 1100,
        flagged_not_churned: 900,
        observed: { customers: 880, churned: 528, rate: 0.6 },
      },
      {
        ...BANDS[3],
        customers: 400,
        share: 400 / 7000,
        monthly_charges: 30_000,
        monthly_share: 30 / 450,
        flagged: 400,
        flagged_not_churned: 300,
        observed: { customers: 320, churned: 272, rate: 0.85 },
      },
    ],
    histogram: Array.from({ length: 20 }, (_, i) => ({
      low: i / 20,
      high: (i + 1) / 20,
      band: BANDS[Math.min(3, Math.floor(i / 5))]?.name ?? 'Low',
      customers: 350,
      monthly_charges: 22_500,
      // The top bin holds too few labelled customers to report a rate.
      observed:
        i === 19
          ? { customers: 12, churned: null, rate: null }
          : { customers: 280, churned: 14 * (i + 1), rate: (14 * (i + 1)) / 280 },
      observed_mean: i === 19 ? null : (i + 0.8) / 20,
    })),
    segments: [
      {
        key: 'Contract',
        partition_customers: 4200,
        base_rate: 0.25,
        spread: 17,
        peak: 'Month-to-month',
        levels: [
          reported('Month-to-month', 2300, 1000),
          reported('One year', 870, 90),
          reported('Two year', 1030, 30),
        ],
      },
      {
        key: 'PaymentMethod',
        partition_customers: 4200,
        base_rate: 0.25,
        spread: 3.2,
        peak: 'Electronic check',
        levels: [
          reported('Electronic check', 1400, 630),
          reported('Mailed check', 980, 190),
          // The small level, and the one held back so it cannot be worked out.
          {
            row: 'Bank transfer (automatic)',
            column: null,
            n: 920,
            suppressed: true,
            reason: 'withheld',
          },
          {
            row: 'Credit card (automatic)',
            column: null,
            n: 12,
            suppressed: true,
            reason: 'too_few',
          },
        ],
      },
      {
        key: 'InternetService',
        partition_customers: 4200,
        base_rate: 0.25,
        spread: 5.8,
        peak: 'Fiber optic',
        levels: [
          reported('Fiber optic', 1840, 780),
          reported('DSL', 1470, 270),
          reported('No', 890, 66),
        ],
      },
      {
        key: 'monthly',
        partition_customers: 4200,
        base_rate: 0.25,
        spread: 3.5,
        peak: '$75-95',
        levels: [
          reported('<$35', 1030, 110),
          reported('$35-55', 545, 150),
          reported('$55-75', 775, 200),
          reported('$75-95', 1090, 410),
          reported('$95+', 760, 250),
        ],
      },
    ],
    lifecycle: {
      partition_customers: 4200,
      base_rate: 0.25,
      median_tenure_churned: 10,
      median_tenure_stayed: 37,
      buckets: [
        reported('0-3', 640, 360),
        reported('4-6', 300, 120),
        reported('7-12', 560, 190),
        reported('13-24', 760, 170),
        reported('25-48', 1010, 140),
        reported('49-72', 930, 70),
      ],
    },
    importance: [
      { feature: 'tenure', label: 'Tenure (months)', mean_abs_shap: 0.149, share: 0.3 },
      { feature: 'MonthlyCharges', label: 'Monthly charges', mean_abs_shap: 0.081, share: 0.16 },
      { feature: 'InternetService', label: 'Internet service', mean_abs_shap: 0.066, share: 0.13 },
      { feature: 'Contract', label: 'Contract type', mean_abs_shap: 0.052, share: 0.1 },
      { feature: 'TotalCharges', label: 'Total charges to date', mean_abs_shap: 0.04, share: 0.08 },
      { feature: 'PaymentMethod', label: 'Payment method', mean_abs_shap: 0.021, share: 0.04 },
    ],
    importance_basis: { partition: 'validation', customers: 300 },
    association: [
      { feature: 'Contract', measure: "Cramer's V", strength: 0.42 },
      { feature: 'tenure', measure: 'point-biserial |r|', strength: 0.35 },
      { feature: 'InternetService', measure: "Cramer's V", strength: 0.32 },
      { feature: 'PaymentMethod', measure: "Cramer's V", strength: 0.3 },
      { feature: 'OnlineSecurity', measure: "Cramer's V", strength: 0.35 },
      { feature: 'TechSupport', measure: "Cramer's V", strength: 0.34 },
    ],
    campaign: {
      threshold: 0.36,
      customers: 1400,
      flagged: 450,
      precision: 0.58,
      recall: 0.695,
      intervention_cost: 22_500,
      retained_value: 48_900,
      net_benefit: 26_400,
      peak_threshold: 0.3,
      peak_net_benefit: 26_432,
      peak_gain: 32,
      recommended_threshold: 0.36,
      assumptions: {
        currency: 'USD',
        retention_offer_cost: 50,
        offer_success_rate: 0.3,
        expected_horizon_months: 12,
        gross_margin: 0.65,
      },
      curve: curve(),
    },
    withheld_reason: null,
    ...overrides,
  };
}

export function customerRow(id: string, overrides: Partial<CustomerRow> = {}): CustomerRow {
  return {
    id,
    contract: 'Month-to-month',
    internet_service: 'Fiber optic',
    payment_method: 'Electronic check',
    tenure: 2,
    monthly_charges: 90,
    churn_probability: 0.8,
    risk_band: 'Critical',
    flagged: true,
    partition: 'train',
    outcome: 'stayed',
    status: 'active',
    at_risk: 72,
    ...overrides,
  };
}

export function customerPage(
  rows: CustomerRow[],
  overrides: Partial<CustomerPage> = {},
): CustomerPage {
  return {
    total: rows.length,
    page: 1,
    page_size: 8,
    pages: Math.max(1, Math.ceil(rows.length / 8)),
    rows,
    band_counts: {},
    status_counts: {
      active: rows.filter((row) => row.status === 'active').length,
      churned: rows.filter((row) => row.status === 'churned').length,
      unknown: rows.filter((row) => row.status === 'unknown').length,
    },
    summary: {
      customers: rows.length,
      share_of_book: rows.length / 7000,
      flagged: rows.length,
      flagged_share: 1,
      mean_probability: 0.8,
      expected_churners: rows.length * 0.8,
      observed: null,
      monthly_charges: rows.length * 90,
      active_monthly_charges: rows.length * 90,
      at_risk: rows.length * 72,
      histogram: Array.from({ length: 20 }, () => 0),
    },
    withheld_reason: null,
    ...overrides,
  };
}

/** Explanations for ``ids``: tenure raises the score, monthly charges lower it. */
export function explanations(ids: readonly string[]): ExplainResponse {
  return {
    model_sha256: 'a'.repeat(64),
    background_customers: 100,
    background: 'training',
    permutations: 10,
    seed: 42,
    caveat: 'Attributions explain the model, not the customer.',
    explanations: ids.map((id) => ({
      customer_id: id,
      base_value: 0.26,
      churn_probability: 0.8,
      contributions: [
        { feature: 'tenure', label: 'Tenure (months)', value: 2, shap_value: 0.317 },
        { feature: 'MonthlyCharges', label: 'Monthly charges', value: 90, shap_value: -0.209 },
        { feature: 'Contract', label: 'Contract type', value: 'Month-to-month', shap_value: 0.12 },
      ],
    })),
  };
}
