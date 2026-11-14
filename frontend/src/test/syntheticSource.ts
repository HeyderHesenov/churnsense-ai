/**
 * The service as a public demo answers: generated customers listed, the
 * dataset shown as aggregates. The sentences are the fixture's own, not the
 * service's, so a test that finds them on a page proves the page printed what
 * the API sent rather than words of its own.
 */
import type { CustomerRow, DataSource } from '../api/queries';
import { modelInfo } from './fixtures';
import { answer } from './server';

export const SYNTHETIC: DataSource = {
  customer_records: 'synthetic',
  customers: 2000,
  title: 'Generated demo customers (fixture)',
  notice: 'Fixture notice: every customer listed was generated.',
  figures_note: 'Fixture figures note: illustrative counts of generated customers.',
  evidence_note: 'Fixture evidence note: aggregates of the sample, no row served.',
};

export const DATASET: DataSource = {
  customer_records: 'dataset',
  customers: 7000,
  title: 'IBM Telco sample',
  notice: null,
  figures_note: null,
  evidence_note: null,
};

/** ``/api/model-info`` saying the customers listed are generated ones. */
export function syntheticInfo(source: DataSource = SYNTHETIC) {
  return answer('get', '/api/model-info', modelInfo({ data_source: source }));
}

/** A row as the service lists a generated customer: no partition of the dataset, no outcome. */
export function asSynthetic(row: CustomerRow): CustomerRow {
  return {
    ...row,
    partition: 'synthetic',
    outcome: 'withheld',
    status: 'active',
    at_risk: Number((row.churn_probability * row.monthly_charges).toFixed(2)),
  };
}
