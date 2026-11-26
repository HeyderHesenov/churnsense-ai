/**
 * The canonical account snapshot contract (``account_snapshot.v0``), as the
 * Data Hub's column mapping offers it. The API does not list its fields in a
 * response, so the list is written here and held to the API's own schema at
 * compile time: ``FIELD_LABELS`` must name exactly the fields of the
 * generated ``AccountSnapshot`` (the row number aside), and ``REQUIRED`` must
 * be exactly its required ones. A field added, removed or made optional in the
 * API fails the type check after ``npm run openapi:v2``.
 *
 * Which column feeds which field, and whether a mapping is valid, is the
 * server's to decide: nothing here checks a mapping.
 */
import type { AccountSnapshot, AccountStatus } from './api';

export type CanonicalField = Exclude<keyof AccountSnapshot, 'row'>;

/** The fields a snapshot must have: those the schema does not mark optional. */
type RequiredField = {
  [K in CanonicalField]-?: object extends Pick<AccountSnapshot, K> ? never : K;
}[CanonicalField];

/** Each field's plain name; the order is the contract's own. */
export const FIELD_LABELS = {
  account_external_id: 'Account ID',
  as_of_date: 'Snapshot date',
  status: 'Status',
  mrr: 'MRR',
  currency: 'Currency',
  account_name: 'Account name',
  customer_since: 'Customer since',
  renewal_date: 'Renewal date',
  canceled_at: 'Canceled at',
  cancellation_reason: 'Cancellation reason',
  segment: 'Segment',
  company_size_band: 'Company size band',
  industry: 'Industry',
  active_users: 'Active users',
  seats_purchased: 'Seats purchased',
  sessions_30d: 'Sessions, 30 days',
  key_actions_30d: 'Key actions, 30 days',
  last_activity_at: 'Last activity',
  usage_period_start: 'Usage period start',
  usage_period_end: 'Usage period end',
} as const satisfies Record<CanonicalField, string>;

// Both directions: no field of the schema missing here, and none here it lacks.
type Exact<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
const LABELS_MATCH_SCHEMA: Exact<keyof typeof FIELD_LABELS, CanonicalField> = true;

export const REQUIRED = [
  'account_external_id',
  'as_of_date',
  'status',
  'mrr',
  'currency',
] as const satisfies readonly RequiredField[];
const REQUIRED_MATCH_SCHEMA: Exact<(typeof REQUIRED)[number], RequiredField> = true;

export const CANONICAL_FIELDS = Object.keys(FIELD_LABELS) as CanonicalField[];

export function isRequired(field: string): boolean {
  return (REQUIRED as readonly string[]).includes(field);
}

/** The statuses a status mapping may translate to, in the contract's order. */
export const STATUSES = [
  'trialing',
  'active',
  'past_due',
  'paused',
  'canceled',
] as const satisfies readonly AccountStatus[];
const STATUSES_MATCH_SCHEMA: Exact<(typeof STATUSES)[number], AccountStatus> = true;

/** For the tests: the three compile-time checks, so none is an unused binding. */
export const SCHEMA_CHECKS = [LABELS_MATCH_SCHEMA, REQUIRED_MATCH_SCHEMA, STATUSES_MATCH_SCHEMA];
