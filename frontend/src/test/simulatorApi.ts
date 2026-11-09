/**
 * The simulator's API, answered from fixtures: a sweep of 1,250 synthetic
 * validation customers (310 churners) over the API's 101-point grid, worked
 * out for whatever assumptions the request sends, and echoing them as the
 * server does. Within a point every count and every amount differs, so a
 * figure shown from the wrong field is caught.
 *
 * The recommendation, peak, band and sensitivity follow the server's rule
 * (``insights.economics``): the page itself never works any of them out.
 */
import type { Sweep } from '../api/queries';
import { respond } from './server';

type Point = Sweep['points'][number];
type Echo = Sweep['assumptions'];

export const CUSTOMERS = 1250;
export const CHURNERS = 310;
/** What a retained customer is assumed to pay a month, in the fixture. */
const MONTHLY = 70;

export const DEFAULTS: Echo = {
  currency: 'USD',
  retention_offer_cost: 50,
  offer_success_rate: 0.3,
  expected_horizon_months: 12,
  gross_margin: 0.65,
};

/** numpy's ``linspace(0, 1, 101)``: ``i * 0.01``, so 0.35 is 0.35000000000000003. */
export function gridAt(i: number): number {
  return i === 100 ? 1 : i * 0.01;
}

function pointAt(i: number, echo: Echo): Point {
  const t = gridAt(i);
  const flagged = Math.round(CUSTOMERS * (1 - t) ** 2);
  const tp = Math.min(flagged, Math.round(CHURNERS * (1 - t ** 3)));
  const precision = flagged === 0 ? 0 : tp / flagged;
  const recall = tp / CHURNERS;
  const value = MONTHLY * echo.expected_horizon_months * echo.gross_margin;
  const retained = tp * value * echo.offer_success_rate;
  const cost = flagged * echo.retention_offer_cost;
  return {
    threshold: t,
    flagged,
    true_positives: tp,
    false_positives: flagged - tp,
    false_negatives: CHURNERS - tp,
    precision,
    recall,
    f1: precision + recall === 0 ? 0 : (2 * precision * recall) / (precision + recall),
    intervention_cost: cost,
    retained_value: retained,
    net_benefit: retained - cost,
  };
}

/** A point as ``recommended`` and ``peak`` carry it: without FP, FN or F1. */
function scenario(point: Point): Sweep['recommended'] {
  return {
    threshold: point.threshold,
    flagged: point.flagged,
    true_positives: point.true_positives,
    precision: point.precision,
    recall: point.recall,
    intervention_cost: point.intervention_cost,
    retained_value: point.retained_value,
    net_benefit: point.net_benefit,
  };
}

/** The server's rule: the highest cut within one offer's cost of the best net. */
function choose(points: readonly Point[], cost: number) {
  const peak = points.reduce((best, p) => (p.net_benefit > best.net_benefit ? p : best));
  const near = points.filter((p) => p.net_benefit >= peak.net_benefit - cost);
  const recommended = near.reduce((high, p) => (p.threshold > high.threshold ? p : high));
  return { peak, near, recommended };
}

/** The sweep the server would answer for ``echo``; ``points`` replaces points by index. */
export function sweepFor(
  echo: Echo = DEFAULTS,
  points: Record<number, Partial<Point>> = {},
): Sweep {
  const all = Array.from({ length: 101 }, (_, i) => ({ ...pointAt(i, echo), ...points[i] }));
  const { peak, near, recommended } = choose(all, echo.retention_offer_cost);
  const sensitivity = [0.1, 0.2, 0.3, 0.4, 0.5].map((rate) => {
    const under = Array.from({ length: 101 }, (_, i) =>
      pointAt(i, { ...echo, offer_success_rate: rate }),
    );
    const at = choose(under, echo.retention_offer_cost).recommended;
    return {
      offer_success_rate: rate,
      threshold: at.threshold,
      flagged: at.flagged,
      precision: at.precision,
      recall: at.recall,
      net_benefit: at.net_benefit,
    };
  });
  return {
    assumptions: echo,
    customers: CUSTOMERS,
    churners: CHURNERS,
    shipped_threshold: 0.36,
    points: all,
    recommended: scenario(recommended),
    peak: scenario(peak),
    band: {
      low: Math.min(...near.map((p) => p.threshold)),
      high: Math.max(...near.map((p) => p.threshold)),
      // The server's floor: the peak's net less one offer, whichever cuts reach it.
      floor: peak.net_benefit - Math.max(echo.retention_offer_cost, 0),
    },
    sensitivity,
    disclaimer: 'SIMULATED: the offer success rate is an assumption, not an estimate.',
  };
}

/** The assumptions a sweep request sends, read as the server reads them (unset: the default). */
export function echoOf(request: Request): Echo {
  const query = new URL(request.url).searchParams;
  const read = (key: string, fallback: number) => {
    const raw = query.get(key);
    return raw === null ? fallback : Number(raw);
  };
  return {
    currency: DEFAULTS.currency,
    retention_offer_cost: read('cost', DEFAULTS.retention_offer_cost),
    offer_success_rate: read('success', DEFAULTS.offer_success_rate),
    expected_horizon_months: read('horizon', DEFAULTS.expected_horizon_months),
    gross_margin: read('margin', DEFAULTS.gross_margin),
  };
}

/** ``GET /api/threshold/sweep`` answered for whatever it is asked. */
export function sweepApi(make: (echo: Echo) => Sweep = (echo) => sweepFor(echo)) {
  return respond('get', '/api/threshold/sweep', (request) => make(echoOf(request)));
}
