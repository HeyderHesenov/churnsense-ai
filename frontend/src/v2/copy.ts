/**
 * The V2 Preview's own words, in one place so the tests can hold them to the
 * project's rules: no causal claim, no forecast of lost or saved revenue, and
 * the two experiences never presented as one.
 */

/** The breadcrumb's section on every V2 page. */
export const SECTION = 'V2 Preview';

export const PREVIEW_NOTE = {
  title: 'Local preview.',
  body: 'This workspace runs only on this machine, behind two local switches. Nothing you upload is saved, and the portfolio is synthetic demo data.',
};

export const BOUNDARY = {
  title: 'Two separate tools.',
  body: 'A file you check in the Data Hub is not added to the portfolio demo, and the portfolio demo never reads it. Nothing is stored between requests, so nothing connects them.',
};

export const NOT_SAVED = {
  title: 'Checked, not saved.',
  body: 'The file is sent to the local server, checked in memory and discarded with the response. Nothing is imported, stored or added to the portfolio demo.',
};

export const SYNTHETIC = {
  title: 'Synthetic demo data.',
  body: 'Generated SaaS accounts for this preview, not your file and not real customers. Levels come from transparent rules, not a model: they say what was observed, not why an account may leave.',
};

export const EXPOSED_MRR =
  'Exposed MRR is the gross monthly recurring revenue of accounts showing attention or watch signals. It is not a forecast of lost revenue, and not revenue saved or recoverable.';

export const LEVEL_LABEL = {
  attention: 'Attention',
  watch: 'Watch',
  no_signals: 'No signals',
  not_assessed: 'Not assessed',
} as const;

export const STATUS_LABEL = {
  trialing: 'Trialing',
  active: 'Active',
  past_due: 'Past due',
  paused: 'Paused',
  canceled: 'Canceled',
} as const;

export const PREVIEW_STATUS = {
  valid: {
    title: 'Every row passed',
    body: 'Every data row matches the account contract.',
  },
  partially_valid: {
    title: 'Some rows were rejected',
    body: 'The accepted rows match the account contract; each rejected row is listed with its reason.',
  },
  invalid: {
    title: 'No row passed',
    body: 'No data row matches the account contract. Each problem is listed below.',
  },
  mapping_required: {
    title: 'Map the required fields',
    body: 'A required field could not be matched to a column, so no row was checked yet. Choose the columns below and check the file again.',
  },
} as const;
