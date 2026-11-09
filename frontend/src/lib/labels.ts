/**
 * How the design words each feature and category, per context.
 *
 * The API speaks in the data contract's column names and category values; the
 * artboards use shorter words in tight places (a table cell, a chip) and
 * fuller ones where there is room. These tables are copy, not data: every
 * value they describe comes from the API.
 */
import { cents, months } from './format';

export type Feature =
  | 'SeniorCitizen'
  | 'Partner'
  | 'Dependents'
  | 'tenure'
  | 'PhoneService'
  | 'MultipleLines'
  | 'InternetService'
  | 'OnlineSecurity'
  | 'OnlineBackup'
  | 'DeviceProtection'
  | 'TechSupport'
  | 'StreamingTV'
  | 'StreamingMovies'
  | 'Contract'
  | 'PaperlessBilling'
  | 'PaymentMethod'
  | 'MonthlyCharges'
  | 'TotalCharges';

/** Every feature, in the column contract's order (the order the API lists them in). */
export const FEATURES: readonly Feature[] = [
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
];

/** Feature names where there is room: charts, rankings, the explainability page. */
export const FEATURE: Record<Feature, string> = {
  tenure: 'Tenure',
  MonthlyCharges: 'Monthly charges',
  TotalCharges: 'Total charges',
  SeniorCitizen: 'Senior citizen',
  Partner: 'Partner',
  Dependents: 'Dependents',
  PhoneService: 'Phone service',
  MultipleLines: 'Multiple lines',
  InternetService: 'Internet service',
  OnlineSecurity: 'Online security',
  OnlineBackup: 'Online backup',
  DeviceProtection: 'Device protection',
  TechSupport: 'Tech support',
  StreamingTV: 'Streaming TV',
  StreamingMovies: 'Streaming movies',
  Contract: 'Contract',
  PaperlessBilling: 'Paperless billing',
  PaymentMethod: 'Payment method',
};

/** The overview's rankings name two features the long way. */
export const RANKING: Record<Feature, string> = {
  ...FEATURE,
  Contract: 'Contract type',
  TotalCharges: 'Total charges to date',
};

/** Feature names in tight places: the explorer's detail panel and table. */
export const COMPACT: Record<Feature, string> = {
  ...FEATURE,
  PhoneService: 'Phone',
  InternetService: 'Internet',
  PaymentMethod: 'Payment',
};

/** One-word names for attribution chips in the retention queue. */
export const CHIP: Record<Feature, string> = {
  ...COMPACT,
  MonthlyCharges: 'Monthly',
  TotalCharges: 'Total',
  OnlineSecurity: 'Security',
  OnlineBackup: 'Backup',
  DeviceProtection: 'Protection',
  TechSupport: 'Support',
  StreamingTV: 'TV',
  StreamingMovies: 'Movies',
  MultipleLines: 'Lines',
  PaperlessBilling: 'Paperless',
  SeniorCitizen: 'Senior',
};

const SHORTER: Record<string, string> = {
  'Tenure (months)': 'Tenure',
  'Total charges to date': 'Total charges',
  'Contract type': 'Contract',
};

/**
 * The single-customer page's sentences name a contribution with the
 * library's label, shortened where the artboard shortens it ("Tenure
 * (months)" -> "Tenure"); "Tech support add-on" stays whole.
 */
export function sentenceLabel(label: string): string {
  return SHORTER[label] ?? label;
}

/** Its waterfall shortens the add-ons too ("Tech support add-on" -> "Tech support"). */
export function contributionLabel(label: string): string {
  return sentenceLabel(label).replace(' add-on', '');
}

export function isFeature(value: string): value is Feature {
  return Object.hasOwn(FEATURE, value);
}

const INTERNET_SHORT: Record<string, string> = {
  'Fiber optic': 'Fiber',
  DSL: 'DSL',
  No: 'No internet',
};
const PAYMENT_SHORT: Record<string, string> = {
  'Electronic check': 'E-check',
  'Mailed check': 'Mailed check',
  'Bank transfer (automatic)': 'Bank transfer',
  'Credit card (automatic)': 'Card',
};

/** "Month-to-month · Fiber · E-check": a customer's profile in one line. */
export function profile(contract: string, internet: string, payment: string): string {
  return `${contract} · ${INTERNET_SHORT[internet] ?? internet} · ${PAYMENT_SHORT[payment] ?? payment}`;
}

/** A listing's partition filter: every partition, or one (the test one says its outcomes are withheld). */
export const PARTITION_OPTIONS = [
  { value: 'all', label: 'All' },
  { value: 'train', label: 'Train' },
  { value: 'validation', label: 'Validation' },
  { value: 'test', label: 'Test (outcome withheld)' },
] as const;

/** A category value as the profile tables write it: "(automatic)" becomes "(auto)". */
export function categoryValue(value: string): string {
  return value.replace(' (automatic)', ' (auto)');
}

/** A feature's value as the API sends it (a number or a word), written for a reader. */
export function valueText(feature: string, value: string | number): string {
  if (feature === 'tenure') return months(Number(value));
  if (feature === 'MonthlyCharges' || feature === 'TotalCharges') return cents(Number(value));
  if (feature === 'SeniorCitizen') return String(value) === '1' ? 'Yes' : 'No';
  return categoryValue(String(value));
}

/** How a breakdown names its levels on the churn-patterns page. */
const LEVEL: Record<string, Record<string, string>> = {
  InternetService: { No: 'None' },
  PaymentMethod: {
    'Electronic check': 'E-check',
    'Bank transfer (automatic)': 'Bank transfer',
    'Credit card (automatic)': 'Credit card',
  },
  SeniorCitizen: { '0': 'No', '1': 'Yes' },
};

/** A breakdown level for display: "0-3" -> "0–3", "<$35" -> "< $35", "No internet service" -> "No internet". */
export function level(dimension: string, value: string): string {
  const mapped = LEVEL[dimension]?.[value];
  if (mapped !== undefined) return mapped;
  if (dimension === 'tenure' || dimension === 'monthly') {
    return value.replace('<', '< ').replace('-', '–');
  }
  return value
    .replace('No internet service', 'No internet')
    .replace('No phone service', 'No phone');
}

/** The overview's segment tabs word some levels more fully. */
export function overviewLevel(dimension: string, value: string): string {
  if (dimension === 'PaymentMethod') return categoryValue(value);
  if (dimension === 'InternetService' && value === 'No') return 'No internet';
  if (dimension === 'monthly')
    return value.startsWith('<') ? `Under ${value.slice(1)}` : value.replace('-', '–');
  return value;
}

/** Breakdown dimensions, as the churn-patterns selects name them, in their order. */
export const DIMENSIONS: readonly (readonly [string, string])[] = [
  ['Contract', 'Contract'],
  ['tenure', 'Tenure (months)'],
  ['monthly', 'Monthly charges'],
  ['InternetService', 'Internet service'],
  ['PaymentMethod', 'Payment method'],
  ['OnlineSecurity', 'Online security'],
  ['TechSupport', 'Tech support'],
  ['PaperlessBilling', 'Paperless billing'],
  ['SeniorCitizen', 'Senior citizen'],
  ['Partner', 'Partner'],
  ['Dependents', 'Dependents'],
  ['StreamingTV', 'Streaming TV'],
  ['MultipleLines', 'Multiple lines'],
  ['PhoneService', 'Phone service'],
];

/** A breakdown dimension's display name. */
export function dimensionLabel(key: string): string {
  return DIMENSIONS.find(([k]) => k === key)?.[1] ?? key;
}

/** Candidate model names as the performance page writes them. */
export function modelName(name: string): string {
  const names: Record<string, string> = {
    'Hist Gradient Boosting': 'Hist. gradient boosting',
    'Random Forest': 'Random forest',
    'Logistic Regression': 'Logistic regression',
    'Baseline (predicts the prior)': 'Baseline (prior)',
  };
  return names[name] ?? name;
}
