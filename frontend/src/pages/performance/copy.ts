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

/** The note over a page that shows a recorded test evaluation. */
export const REUSED_HOLDOUT = 'A re-used holdout, not an untouched one.';

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

// --- the partitions and their tiles ------------------------------------------------

export const PARTITION_NAMES = { validation: 'Validation', test: 'Test' } as const;

export const VALIDATION_NOTE =
  'Measured live on the validation customers, which set the cut and decided calibration, so these figures are somewhat optimistic. Ranking and probability figures do not depend on the cut. Churn is the positive class throughout.';

export const TEST_NOTE =
  'The recorded test release at its one cut: the two-by-two table and the rates it gives. Accuracy is listed last: it counts every customer left alone who stayed as right.';

export const NO_TEST = 'No test figures shown';

export function churnersFlagged(tp: number, positives: number): string {
  return `${int(tp)} of ${int(positives)} churners flagged`;
}

/** Who the flagged turned out to be, from the two cells, never their sum. */
export function flaggedOutcomes(tp: number, fp: number): string {
  return `of the flagged, ${int(tp)} churned and ${int(fp)} stayed`;
}

export const TEST_SCOPE =
  "Test customers are listed with their flags and scores, so a figure about any group of them is read against known people. The test record holds the partition's own table only: no ranking score, segment, bin, calibration error or second cut is published for it.";

// --- the test record, one square per customer ----------------------------------------

/** What each outcome is called on the waffle, the cards and the matrix. */
export const OUTCOME_NAMES = {
  tp: { short: 'caught', long: 'flagged, churned' },
  fn: { short: 'missed', long: 'not flagged, churned' },
  fp: { short: 'offers to stayers', long: 'flagged, stayed' },
  tn: { short: 'left alone', long: 'not flagged, stayed' },
} as const;

export const WAFFLE_MISMATCH =
  "The record's four cells do not add up to its customers, so no square-per-customer picture is drawn.";

export function waffleSubtitle(n: number, threshold: number): string {
  return `Each square is one of the ${int(n)} test customers, by what the cut ${cut(threshold)} did.`;
}

export function waffleLabel(
  counts: { tp: number; fn: number; fp: number; tn: number },
  threshold: number,
): string {
  return (
    `Test customers at the cut ${cut(threshold)}, one square each: ` +
    `${int(counts.tp)} caught, ${int(counts.fn)} missed, ` +
    `${int(counts.fp)} offers to stayers, ${int(counts.tn)} left alone.`
  );
}

/** The three plain-language cards: raw counts of the record, no ratio or rescaling. */
export function plainCaught(tp: number, positives: number): string {
  return `Of the ${int(positives)} test customers who churned, ${int(tp)} were flagged.`;
}

export function plainOffers(tp: number, fp: number): string {
  return `Among the customers flagged, ${int(tp)} churned and ${int(fp)} stayed.`;
}

export function plainMissed(fn: number, threshold: number): string {
  return `${int(fn)} test customers who churned scored below the cut ${cut(threshold)} and were not flagged.`;
}

// --- errors at a cut --------------------------------------------------------------

export function offerCost(cost: number, currency: string): string {
  return `${money(cost, currency)} each if offered, assumed`;
}

/** The explored validation cut in one line: the sweep point's own figures. */
export function readoutAt(
  threshold: number,
  flagged: number,
  precision: string,
  recall: string,
  f1: string,
): string {
  return `At ${cut(threshold)}: ${int(flagged)} flagged · precision ${precision} · recall ${recall} · F1 ${f1}.`;
}

/** The test record's table in one line, cell by cell. */
export function recordAt(threshold: number, tp: number, fn: number, fp: number): string {
  return `At ${cut(threshold)}: ${int(tp)} churners caught, ${int(fn)} missed; ${int(fp)} offers to stayers.`;
}

export const ONE_CUT =
  'The test release is recorded at one cut only: no other cut, curve or ranking score of it is published.';

export const NOT_OPTIMAL = 'One cut under one set of assumptions, not a universal optimum.';

export const PR_HINT =
  'Hover to read a cut; a click loads it into the fader, the keyboard way to move the cut.';

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

export function segmentsTitle(threshold: number): string {
  return `Performance by segment at ${cut(threshold)}`;
}

export function segmentsSubtitle(threshold: number): string {
  return `Does an aggregate recall hide a segment the model never flags? Validation customers at the cut ${cut(threshold)}.`;
}

export const UNSCANNED =
  'Not scanned: nobody in the segment is flagged although some churned. Not scanned is not the same as no risk. Precision is “—” where nothing is flagged: undefined, not zero.';

export const SMALL = 'fewer than 20 customers';

export const GENDER =
  'Gender is not a model input; it is shown because leaving a column out does not show the groups are treated alike. A descriptive check, not a fairness certification.';

// --- footer -----------------------------------------------------------------------

export const FOOTER =
  'This page computes nothing from the test partition; test figures, when shown, are the record make evaluate CONFIRM=yes wrote for this artifact.';
