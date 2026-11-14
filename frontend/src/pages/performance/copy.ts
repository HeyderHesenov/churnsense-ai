/**
 * The performance page's words. Each sentence that carries a figure writes
 * it from a served field through ``lib/format``; none claims more than the
 * evaluation supports (a re-used holdout is never "independent", a cut is
 * never "optimal", a probability is similarity to past churners, not cause).
 */
import { cut, int, money } from '../../lib/format';
import type { Calibration, Candidate, Cut, Selection } from './performance';
import { binLabel, precisionOf, recallOf, score, type Bin } from './performance';

export const TITLE = 'How good is the ranking, and where is it blind?';

export function lede(
  folds: number | null,
  train: number,
  validation: number,
  recorded: boolean,
): string {
  return (
    `Chosen by ${folds === null ? '' : `${folds}-fold `}cross-validation on ${int(train)} ` +
    `training customers; the cut, and whether to keep calibration, were decided on ` +
    `${int(validation)} validation customers. ` +
    (recorded
      ? "Test figures are the partition's one recorded release, counts and rates only, read on a re-used holdout."
      : 'No test evaluation is shown for this model, so no figure here is a test result.')
  );
}

export const ROLES = {
  train: 'Chose the model and tuned it, by cross-validation.',
  validation:
    'Set the cut and decided whether to keep calibration, so its figures are somewhat optimistic, threshold figures most.',
  recorded: 'A re-used holdout: a re-checked estimate, not an independent one.',
} as const;

/** "02b2466d…47a9": enough of a SHA-256 to tell two artifacts apart. */
export function shortSha(sha: string): string {
  return sha.length > 12 ? `${sha.slice(0, 8)}…${sha.slice(-4)}` : sha;
}

export function evaluatedOn(iso: string): string {
  return iso.slice(0, 10);
}

/** Beside a test figure that does not exist: not a dash that might mean "not computed yet". */
export const NOT_PUBLISHED = 'not published for test';

export function withheld(reason: string): string {
  return `Validation outcomes are withheld: ${reason}`;
}

/** Why a validation figure is not shown: outcomes withheld, no published sweep, or neither. */
export function unavailableText(withheldReason: string | null, sweepNote: string | null): string {
  if (withheldReason) return withheld(withheldReason);
  return sweepNote ?? UNAVAILABLE;
}

export const UNAVAILABLE = 'Validation figures are not available for the served model.';

export const MISMATCH =
  'The served model changed while this page loaded: its figures and its description no longer describe the same artifact.';

export const FAILED = 'The model’s evaluation could not be loaded, so nothing can be shown yet.';

// --- the scorecard ----------------------------------------------------------------

export const SCORE_GROUPS = {
  ranking: 'Ranking · threshold-free',
  cut: 'At the cut',
  probabilities: 'Probabilities',
  context: 'Context',
} as const;

export const SCORE_NOTE =
  'Ranking metrics do not depend on the cut; the figures at the cut do. Churn is the positive class throughout. Accuracy is listed last: with most customers staying, flagging nobody already scores high.';

export function churnersFlagged(tp: number, positives: number): string {
  return `${int(tp)} of ${int(positives)} churners flagged`;
}

export function flaggedChurned(tp: number, flagged: number): string {
  return `${int(tp)} of ${int(flagged)} flagged churned`;
}

export function noSkill(rate: number): string {
  return `flagging at random scores ${score(rate)}`;
}

export function nobody(accuracy: number): string {
  return `flagging nobody scores ${score(accuracy)}`;
}

export const TEST_SCOPE =
  "Test customers are listed with their flags and scores, so a figure about any group of them is read against known people. The test column holds the partition's own table only: no segment, bin, calibration error or second cut is published for it. A score metric reads “—” when the scores tie too much to publish it.";

// --- errors at a cut --------------------------------------------------------------

export const CELLS = {
  tn: 'not flagged, stayed',
  fp: 'flagged, stayed',
  fn: 'not flagged, churned',
  tp: 'flagged, churned',
} as const;

export function offerCost(cost: number, currency: string): string {
  return `${money(cost, currency)} each if offered, assumed`;
}

export function errorsAt(threshold: number, flagged: number, fp: number, fn: number): string {
  return `At ${cut(threshold)}: ${int(flagged)} flagged; ${int(fp)} of them stayed, ${int(fn)} churners not flagged.`;
}

export const NOT_OPTIMAL = 'One cut under one set of assumptions, not a universal optimum.';

export const PR_HINT =
  'Hover to read a cut; a click loads it into the errors card, whose slider is the keyboard way to move the cut.';

export function prLabel(
  shipped: Cut | undefined,
  current: Cut | undefined,
  churners: number,
): string {
  const at = (p: Cut) =>
    `precision ${precisionOf(p.flagged, p.precision)} and recall ${recallOf(churners, p.recall)}`;
  return (
    'Validation precision against recall across cuts that flag someone' +
    (shipped ? `; the served cut ${cut(shipped.threshold)} has ${at(shipped)}` : '') +
    (current && current !== shipped
      ? `; the explored cut ${cut(current.threshold)} has ${at(current)}`
      : '') +
    '.'
  );
}

// --- calibration ------------------------------------------------------------------

export function calibrationSentence(calibration: Calibration): string {
  const { method, uncalibrated_ece: before, calibrated_ece: after, epsilon } = calibration;
  const clamp =
    epsilon === null
      ? ''
      : ` Probabilities are clamped to [ε, 1 − ε], ε = ${epsilon.toExponential(1)}.`;
  if (method !== null) {
    const name = method.charAt(0).toUpperCase() + method.slice(1);
    const why =
      before !== null && after !== null
        ? ` because validation ECE on raw probabilities fell from ${score(before)} to ${score(after)}`
        : '';
    return `${name} calibration was kept${why}.${clamp}`;
  }
  if (before !== null && after !== null) {
    return `Calibration was tried and not kept: validation ECE on raw probabilities was ${score(after)} calibrated against ${score(before)} without.${clamp}`;
  }
  return `The served probabilities are not calibrated.${clamp}`;
}

export type ReliabilityBin = Pick<Bin, 'low' | 'high'>;

/** What the calibration chart's bins are: tenths, or the intervals between the cuts a
 * public demo publishes its sweep at (which are uneven, and wider). */
export function calibrationSubtitle(bins: readonly ReliabilityBin[] | null): string {
  const said = 'Observed churn against the mean stated probability, for validation customers';
  if (!bins || bins.length === 0) return `${said}.`;
  const tenths = bins.every((bin) => Math.abs(bin.high - bin.low - 0.1) < 1e-9);
  return tenths
    ? `${said} in tenths of probability.`
    : `${said} between the cuts the sweep is published at.`;
}

/** The bins too small to draw, said where a sighted reader sees the chart. */
export function notDrawn(bins: readonly Bin[]): string {
  const hidden = bins.filter((bin) => bin.rate === null);
  if (hidden.length === 0) return '';
  const listed = hidden.map((bin) => `${binLabel(bin)} (${int(bin.customers)} customers)`);
  return `Not drawn: ${listed.join(', ')}, fewer than 20 customers${hidden.length > 1 ? ' each' : ''}.`;
}

export const AT_HALF_CAVEAT =
  'Brier is the comparable figure; precision and recall at one cut move because calibration rescales the probabilities.';

export const ECE_NOTE =
  'ECE weights every bin, including bins too small to plot; it is measured on the served, clamped probabilities.';

// --- the one-standard-error rule -------------------------------------------------

export function cv(value: number): string {
  return value.toFixed(4);
}

export function selectionSubtitle(folds: number): string {
  return `Which candidates sit within one standard error of the leader? ${folds}-fold cross-validated PR-AUC on training customers, ±1 standard error.`;
}

/**
 * The cutoff, as the leader's score less one standard error when the
 * comparison report's leader agrees with the artifact's record to the digits
 * shown; a report from another training run would make the sum not hold.
 */
export function cutoffCaption(selection: Selection, leader: Candidate | undefined): string {
  if (leader && Math.abs(leader.cv_pr_auc - selection.standard_error - selection.cutoff) < 5e-5)
    return `cutoff ${cv(selection.cutoff)} = ${cv(leader.cv_pr_auc)} − ${cv(selection.standard_error)}`;
  return `cutoff ${cv(selection.cutoff)}`;
}

export function selectionNote(
  leader: Candidate | undefined,
  chosen: Candidate | undefined,
  lead: number | null | undefined,
  name: (model: string) => string,
): string {
  if (!leader || !chosen) return 'The simplest model inside the cutoff is chosen.';
  if (leader === chosen)
    return `${name(chosen.model)} leads and is the simplest model inside the cutoff.`;
  if (lead === null || lead === undefined) return 'The simplest model inside the cutoff is chosen.';
  return `${name(leader.model)} leads ${name(chosen.model)} by ${cv(lead)}, inside one standard error, so the simpler model is chosen.`;
}

export const CANDIDATES_NOTE =
  'Candidates are scored uncalibrated: validation figures here describe the candidates as compared, not the served, calibrated model.';

// --- segments ---------------------------------------------------------------------

export function segmentsSubtitle(threshold: number): string {
  return `Does an aggregate recall hide a segment the model never flags? Customers at the cut ${cut(threshold)}.`;
}

export const UNSCANNED =
  'Not scanned: nobody in the segment is flagged although some churned. Not scanned is not the same as no risk. Precision is “—” where nothing is flagged: undefined, not zero.';

export const SMALL = 'fewer than 20 customers';

export const GENDER =
  'Gender is not a model input; it is shown because leaving a column out does not show the groups are treated alike. A descriptive check, not a fairness certification.';

// --- footer -----------------------------------------------------------------------

export const FOOTER =
  'This page computes nothing from the test partition; test figures, when shown, are the record make evaluate CONFIRM=yes wrote for this artifact.';
