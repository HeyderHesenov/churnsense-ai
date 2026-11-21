import { act, fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw/http';
import type { Performance } from '../../api/queries';
import { App } from '../../app/App';
import { navigate } from '../../app/router';
import { MAIN_ID } from '../../components/shell/Shell';
import { modelInfo } from '../../test/fixtures';
import { entriesAdded } from '../../test/history';
import { curve, measured, performance, performanceApi, recorded } from '../../test/performanceApi';
import { renderAt } from '../../test/render';
import { answer, recordRequests, refuse, server } from '../../test/server';
import {
  FAILED,
  GENDER,
  MISMATCH,
  REUSED_HOLDOUT,
  TEST_SCOPE,
  TITLE,
  UNAVAILABLE,
  UNSCANNED,
} from './copy';
import { PR, prX, prY } from './performance';
import { PERFORMANCE_TABS } from './tabs';

/** The page at ``path``, with ``body`` as the performance response. */
async function showPerformance(path = '/performance', body = performance()) {
  server.use(performanceApi(body));
  const rendered = renderAt(<App />, path);
  await screen.findByRole('heading', { level: 1, name: TITLE });
  await screen.findByRole('tablist', { name: 'Performance sections' });
  return rendered;
}

const RECORDED = performance({ test: recorded(), test_reason: null });

/** The page's own sections, shown only once the figures are in. */
function sections() {
  return screen.queryByRole('tablist', { name: 'Performance sections' });
}

/** The text of each data cell in the row a table names ``header``. */
function cells(table: HTMLElement, header: string | RegExp): string[] {
  const row = within(table).getByRole('rowheader', { name: header }).closest('tr');
  if (!row) throw new Error(`no row ${String(header)}`);
  return within(row)
    .getAllByRole('cell')
    .map((cell) => cell.textContent);
}

/** The cells after the first, in the row of a chart's table whose first cell is ``label``. */
function chartRow(table: HTMLElement, label: string): string[] {
  const row = within(table)
    .getAllByRole('row')
    .find((r) => within(r).queryAllByRole('cell')[0]?.textContent === label);
  if (!row) throw new Error(`no row ${label}`);
  return within(row)
    .getAllByRole('cell')
    .slice(1)
    .map((cell) => cell.textContent);
}

function card(name: string | RegExp) {
  return screen.getByRole('region', { name });
}

/** A tile's whole text (label, value, caption), found by its label. */
function tile(group: HTMLElement, label: string): string {
  const root = within(group).getByText(label, { exact: true }).closest('div');
  if (!root) throw new Error(`no tile ${label}`);
  return root.textContent;
}

/** The value a term names in a card's figure strip. */
function strip(term: string, within_: HTMLElement = document.body): string {
  return within(within_).getByText(term, { selector: 'dt' }).nextElementSibling?.textContent ?? '';
}

function errorsTable() {
  return screen.getByRole('table', { name: /^Errors at / });
}

function matrix(): Record<'tn' | 'fp' | 'fn' | 'tp', string> {
  const table = errorsTable();
  const count = (name: RegExp) =>
    within(table).getByRole('cell', { name }).firstElementChild?.textContent ?? '';
  return {
    tn: count(/not flagged, stayed/),
    fp: count(/(?<!not )flagged, stayed/),
    fn: count(/not flagged, churned/),
    tp: count(/(?<!not )flagged, churned/),
  };
}

function prTable() {
  return screen.getByRole('table', { name: 'Validation precision and recall by cut' });
}

function partition() {
  return screen.getByRole('group', { name: 'Partition' });
}

function search(key: string): string | null {
  return new URLSearchParams(window.location.search).get(key);
}

/** A click on the precision–recall picture at point ``i`` of the fixture curve. */
function pick(i: number) {
  const capture = screen.getByTestId('pr-capture');
  vi.spyOn(capture, 'getBoundingClientRect').mockReturnValue(
    DOMRect.fromRect({ x: 0, y: 0, width: PR.width, height: PR.height }),
  );
  fireEvent.click(capture, { clientX: prX(at(i).recall), clientY: prY(at(i).precision) });
}

/** Every word on the page: none claims a cause, a rescue, a verdict or a made-up ratio. */
function expectNoClaims() {
  const text = document.body.textContent;
  expect(text).not.toMatch(
    /\b(causes|caused by|causing|drives|driven by|driving|will prevent|prevents|guarantee[sd]?)\b/i,
  );
  expect(text).not.toMatch(/\b(saved?|saves|rescued?|prevented|excellent)\b/i);
  expect(text).not.toMatch(/\b\d+ in \d+\b|×|times (better|more)|\blift\b/i);
  for (const sentence of text.split(/(?<=[.:])\s/)) {
    if (/independent/i.test(sentence)) expect(sentence).toMatch(/not an independent/);
    if (/optimum|optimal/i.test(sentence)) expect(sentence).toMatch(/not a universal|test-optimal/);
  }
}

function at(i: number) {
  const point = curve().points[i];
  if (!point) throw new Error(`no point ${i}`);
  return point;
}

/** A handler that answers ``body`` only once ``release`` is called. */
function held(path: '/api/performance' | '/api/model-info', body: object) {
  let release: () => void = () => undefined;
  const opened = new Promise<void>((resolve) => (release = resolve));
  gates.push(() => release());
  const handler = http.get(path, async () => {
    await opened;
    return HttpResponse.json(body);
  });
  return { handler, release: () => release() };
}

/** Released after every test, so a failed one never leaves a request waiting. */
const gates: (() => void)[] = [];
afterEach(() => {
  for (const open of gates.splice(0)) open();
});

const EVERY_VIEW = PERFORMANCE_TABS.tabs.flatMap((tab) =>
  ['validation', 'test'].map((part) => `/performance?tab=${tab.value}&part=${part}`),
);

describe('loading and failure', () => {
  it('says it is loading until the evaluation arrives', async () => {
    const slow = held('/api/performance', performance());
    server.use(slow.handler);
    renderAt(<App />, '/performance');
    expect(await screen.findByText('Loading the model’s evaluation')).toBeInTheDocument();
    expect(sections()).not.toBeInTheDocument();
    slow.release();
    expect(
      await screen.findByRole('tablist', { name: 'Performance sections' }),
    ).toBeInTheDocument();
  });

  it('waits for the model’s description too', async () => {
    const slow = held('/api/model-info', modelInfo());
    let answered: () => void = () => undefined;
    const evaluated = new Promise<void>((resolve) => (answered = resolve));
    server.events.on('response:mocked', ({ request }) => {
      if (new URL(request.url).pathname === '/api/performance') answered();
    });
    server.use(performanceApi(), slow.handler);
    renderAt(<App />, '/performance');
    await evaluated;
    // The evaluation is in; the page still waits for the artifact it describes.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getByText('Loading the model’s evaluation')).toBeInTheDocument();
    expect(sections()).toBeNull();
    slow.release();
    expect(
      await screen.findByRole('tablist', { name: 'Performance sections' }),
    ).toBeInTheDocument();
  });

  it('says the evaluation failed in its own words, and a retry keeps the focus on the page', async () => {
    server.use(refuse('get', '/api/performance', 503, 'dataset_unavailable'));
    renderAt(<App />, '/performance');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(FAILED);
    expect(alert).not.toHaveTextContent('Refused by a test');
    server.use(performanceApi());
    await userEvent.click(within(alert).getByRole('button', { name: 'Try again' }));
    expect(document.activeElement?.id).toBe(MAIN_ID);
    expect(
      await screen.findByRole('tablist', { name: 'Performance sections' }),
    ).toBeInTheDocument();
  });

  it('fails the same way when the model’s description cannot be had', async () => {
    server.use(performanceApi(), refuse('get', '/api/model-info', 500, 'internal_error'));
    renderAt(<App />, '/performance');
    expect(await screen.findByText(FAILED)).toBeInTheDocument();
    expect(sections()).not.toBeInTheDocument();
  });

  it('shows no figure when the evaluation describes another artifact than the model', async () => {
    server.use(performanceApi(performance({ model_sha256: 'b'.repeat(64) })));
    renderAt(<App />, '/performance');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(MISMATCH);
    expect(sections()).not.toBeInTheDocument();
    server.use(performanceApi());
    await userEvent.click(within(alert).getByRole('button', { name: 'Reload figures' }));
    expect(
      await screen.findByRole('tablist', { name: 'Performance sections' }),
    ).toBeInTheDocument();
    expect(screen.queryByText(MISMATCH)).not.toBeInTheDocument();
    // The button went with the alert: the focus waits on the page, not the body.
    expect(document.activeElement?.id).toBe(MAIN_ID);
  });

  it.each([
    ['the evaluation', () => performanceApi(performance({ model_sha256: null }))],
    [
      'the model’s description',
      () => answer('get', '/api/model-info', modelInfo({ model_sha256: null })),
    ],
  ])(
    'shows the figures when %s names no artifact: there is nothing to compare',
    async (_, handler) => {
      // The first handler of one call answers: the case's own goes first.
      server.use(handler(), performanceApi());
      renderAt(<App />, '/performance');
      expect(await screen.findByRole('group', { name: 'Validation figures' })).toBeInTheDocument();
      expect(screen.queryByText(MISMATCH)).not.toBeInTheDocument();
    },
  );
});

describe('the tabs and the address', () => {
  it('opens on the summary, and each tab at its own address', async () => {
    const first = await showPerformance();
    const tabs = screen.getByRole('tablist', { name: 'Performance sections' });
    expect(
      within(tabs)
        .getAllByRole('tab')
        .map((tab) => tab.textContent),
    ).toEqual([
      '1Summary The headline figures',
      '2Model choice Why this model',
      '3Errors & trade-offs What each cut-off costs',
      '4Calibration & blind spots Can you trust a score',
    ]);
    expect(within(tabs).getByRole('tab', { selected: true })).toHaveTextContent('Summary');
    expect(card('How well it performs')).toBeInTheDocument();
    first.unmount();

    const expected: Record<string, string | RegExp> = {
      choice: 'Model selection: the one-standard-error rule',
      errors: 'Explore a cut',
      calibration: 'Does a probability mean what it says?',
    };
    for (const [tab, name] of Object.entries(expected)) {
      const shown = await showPerformance(`/performance?tab=${tab}`);
      expect(card(name)).toBeInTheDocument();
      expect(screen.queryByRole('region', { name: 'How well it performs' })).toBeNull();
      shown.unmount();
    }
  });

  it('reads an unknown tab as the summary, and leaves the address alone', async () => {
    await showPerformance('/performance?tab=bogus');
    expect(card('How well it performs')).toBeInTheDocument();
    expect(search('tab')).toBe('bogus');
  });

  it('adds one history entry per tab chosen, and Back and Forward step through them', async () => {
    await showPerformance('/performance?cut=0.50');
    const added = entriesAdded();
    await userEvent.click(screen.getByRole('tab', { name: /Errors & trade-offs/ }));
    expect(search('tab')).toBe('errors');
    expect(added()).toBe(1);
    // The explored cut stays in the address whatever tab is shown.
    expect(search('cut')).toBe('0.50');
    expect(screen.getByText(/^At 0\.50: /)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: /Errors & trade-offs/ }));
    expect(added()).toBe(1);
    act(() => window.history.back());
    expect(await screen.findByRole('region', { name: 'How well it performs' })).toBeVisible();
    act(() => window.history.forward());
    expect(await screen.findByRole('region', { name: 'Explore a cut' })).toBeVisible();
  });

  it('moves between tabs with the arrow keys, Home and End, choosing as it goes', async () => {
    await showPerformance();
    const summary = screen.getByRole('tab', { name: /Summary/ });
    summary.focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: /Model choice/ })).toHaveFocus();
    expect(search('tab')).toBe('choice');
    await userEvent.keyboard('{End}');
    expect(screen.getByRole('tab', { name: /Calibration & blind spots/ })).toHaveFocus();
    expect(search('tab')).toBe('calibration');
    await userEvent.keyboard('{ArrowRight}');
    expect(search('tab')).toBeNull();
    await userEvent.keyboard('{Home}{ArrowLeft}');
    expect(search('tab')).toBe('calibration');
  });
});

describe('the partition', () => {
  it('offers validation and test only, never training', async () => {
    await showPerformance('/performance', RECORDED);
    const buttons = within(partition()).getAllByRole('button');
    expect(buttons.map((button) => button.textContent)).toEqual(['Validation', 'Test']);
    expect(within(partition()).getByRole('button', { name: 'Validation' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.queryByRole('button', { name: /train/i })).toBeNull();
  });

  it('reads a partition it does not offer as validation', async () => {
    await showPerformance('/performance?part=train', RECORDED);
    expect(screen.getByRole('group', { name: 'Validation figures' })).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Test figures' })).toBeNull();
  });

  it('is one choice in the address, shared by the summary and the errors tab', async () => {
    await showPerformance('/performance', RECORDED);
    const added = entriesAdded();
    await userEvent.click(within(partition()).getByRole('button', { name: 'Test' }));
    expect(search('part')).toBe('test');
    expect(added()).toBe(1);
    expect(screen.getByRole('group', { name: 'Test figures' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: /Errors & trade-offs/ }));
    expect(errorsTable()).toHaveAccessibleName('Errors at 0.40, test record');
    act(() => window.history.back());
    expect(await screen.findByRole('group', { name: 'Test figures' })).toBeInTheDocument();
    act(() => window.history.back());
    expect(await screen.findByRole('group', { name: 'Validation figures' })).toBeInTheDocument();
  });
});

describe('where each figure comes from', () => {
  it('names each partition’s job, and says no test evaluation is recorded', async () => {
    await showPerformance();
    const evidence = card('Where each figure comes from');
    expect(evidence).toHaveTextContent('4,225 customers');
    expect(evidence).toHaveTextContent('1,409 customers');
    expect(evidence).toHaveTextContent('somewhat optimistic');
    expect(evidence).toHaveTextContent('Not shown');
    expect(evidence).toHaveTextContent('No test evaluation is recorded for this model.');
    expect(evidence).toHaveTextContent('aaaaaaaa…aaaa');
    expect(evidence).toHaveTextContent('The data and the split match the ones it was trained on.');
  });

  it('says when the data or the split differ, and when provenance is not served', async () => {
    const differs = modelInfo({
      provenance: { verified: false, data_sha256: null, split_digest: null, withheld_reason: 'x' },
    });
    server.use(answer('get', '/api/model-info', differs));
    const first = await showPerformance();
    expect(card('Where each figure comes from')).toHaveTextContent(
      'The data or the split differ from the ones it was trained on.',
    );
    first.unmount();
    server.use(answer('get', '/api/model-info', modelInfo({ provenance: null })));
    await showPerformance();
    expect(card('Where each figure comes from')).toHaveTextContent('Data provenance not served.');
  });

  it('calls a recorded test evaluation a re-used holdout, never an independent one', async () => {
    const first = await showPerformance('/performance', RECORDED);
    const evidence = card('Where each figure comes from');
    expect(evidence).toHaveTextContent('Recorded 2026-10-21');
    expect(evidence).toHaveTextContent('a re-checked estimate, not an independent one');
    const note = screen.getByText(REUSED_HOLDOUT).closest('[role="note"]');
    expect(note).toHaveTextContent('a re-checked estimate, not an independent one');
    first.unmount();
    await showPerformance();
    expect(screen.queryByText(REUSED_HOLDOUT)).toBeNull();
  });

  it('writes the lede from the served counts, and the footer names the artifact', async () => {
    const first = await showPerformance();
    const lede = screen.getByRole('heading', { level: 1 }).nextElementSibling;
    expect(lede).toHaveTextContent(
      /^Chosen by 5-fold cross-validation on 4,225 training customers; .* on 1,409 validation customers\. No test evaluation is shown/,
    );
    expect(screen.getByText('artifact aaaaaaaa…aaaa')).toBeInTheDocument();
    const modelCard = screen.getByRole('link', { name: /Model card/ });
    expect(modelCard).toHaveAttribute('target', '_blank');
    expect(modelCard).toHaveAttribute('rel', 'noopener noreferrer');
    first.unmount();
    await showPerformance('/performance', performance({ ...RECORDED, selection: null }));
    expect(screen.getByRole('heading', { level: 1 }).nextElementSibling).toHaveTextContent(
      /^Chosen by cross-validation on 4,225 .* Test figures are the partition's one recorded release/,
    );
  });
});

describe('the summary: validation', () => {
  it('shows exactly the four ranking and probability figures, each from its own field', async () => {
    await showPerformance('/performance', RECORDED);
    const group = screen.getByRole('group', { name: 'Validation figures' });
    expect(group).toHaveTextContent(/^VALIDATION 1,250 customers, measured live/);
    expect(tile(group, 'PR-AUC (average precision)')).toMatch(
      /^PR-AUC \(average precision\)0\.654primary · precision averaged over recall levels$/,
    );
    expect(tile(group, 'ROC-AUC')).toMatch(/^ROC-AUC0\.841/);
    expect(tile(group, 'Brier score')).toMatch(/^Brier score0\.137/);
    expect(tile(group, 'ECE')).toMatch(/^ECE0\.031/);
    // The figures at the cut are the test record's: validation shows none of them.
    expect(group).not.toHaveTextContent(/Recall|Precision|\bF1\b|Accuracy/);
    // Nor any other validation figure: not its churn rate, nor its no-skill accuracy.
    for (const figure of ['0.601', '0.743', '0.812', '0.733', '0.271']) {
      expect(group).not.toHaveTextContent(figure);
    }
  });

  it('draws PR-AUC on a bar, and nothing else, hidden from screen readers', async () => {
    await showPerformance();
    const group = screen.getByRole('group', { name: 'Validation figures' });
    const bar = group.querySelector('svg');
    expect(bar?.closest('[aria-hidden="true"]')).not.toBeNull();
    const [, fill] = bar?.querySelectorAll('rect') ?? [];
    expect(fill).toHaveAttribute('width', String(measured().metrics.average_precision * 100));
    expect(bar).toHaveAttribute('aria-hidden', 'true');
    expect(bar?.querySelector('line')).toBeNull();
  });

  it('says validation is withheld, with the reason, wherever a validation figure would be', async () => {
    const reason = 'the split differs from the one the model was trained on';
    const withheld = performance({
      validation: null,
      validation_evaluation: null,
      withheld_reason: reason,
    });
    const said = `Validation outcomes are withheld: ${reason}`;
    const views: [string, string][] = [
      ['/performance', 'How well it performs'],
      ['/performance?tab=errors', 'Explore a cut'],
      ['/performance?tab=errors', 'Precision against recall'],
      ['/performance?tab=calibration', 'Performance by segment at 0.36'],
      ['/performance?tab=calibration', 'Does a probability mean what it says?'],
    ];
    for (const [path, name] of views) {
      const shown = await showPerformance(path, withheld);
      expect(card(name)).toHaveTextContent(said);
      expect(screen.queryByRole('slider')).toBeNull();
      shown.unmount();
    }
    await showPerformance('/performance', withheld);
    expect(within(card('Where each figure comes from')).getByRole('note')).toHaveTextContent(said);
  });

  it('says validation is unavailable, without inventing a reason, when nothing is served', async () => {
    const nothing = performance({ validation: null, validation_evaluation: null });
    const first = await showPerformance('/performance', nothing);
    expect(card('How well it performs')).toHaveTextContent(UNAVAILABLE);
    expect(document.body).not.toHaveTextContent('withheld');
    first.unmount();
    await showPerformance('/performance?tab=errors', nothing);
    expect(card('Explore a cut')).toHaveTextContent(UNAVAILABLE);
    expect(card('Precision against recall')).toHaveTextContent(UNAVAILABLE);
    expect(screen.queryByRole('slider')).toBeNull();
  });

  it('says the measured figures are unavailable when only the sweep is served', async () => {
    const sweepOnly = performance({ validation_evaluation: null });
    const first = await showPerformance('/performance', sweepOnly);
    expect(card('How well it performs')).toHaveTextContent(UNAVAILABLE);
    first.unmount();
    // The sweep is a separate figure and still shows.
    const second = await showPerformance('/performance?tab=errors', sweepOnly);
    expect(screen.getByRole('slider', { name: 'Explored cut' })).toBeInTheDocument();
    second.unmount();
    await showPerformance('/performance?tab=calibration', sweepOnly);
    expect(card('Performance by segment at 0.36')).toHaveTextContent(UNAVAILABLE);
  });
});

describe('the summary: test', () => {
  it('shows the record’s rates under TEST, with the counts behind them and nothing else', async () => {
    await showPerformance('/performance?part=test', RECORDED);
    const group = screen.getByRole('group', { name: 'Test figures' });
    expect(group).toHaveTextContent(/^TEST 1,400 customers, recorded 2026-10-21 at the cut 0\.40/);
    expect(tile(group, 'Recall')).toBe('Recall0.712250 of 350 churners flagged');
    expect(tile(group, 'Precision')).toBe(
      'Precision0.499of the flagged, 250 churned and 250 stayed',
    );
    expect(tile(group, 'F1')).toBe('F10.587balance of precision and recall');
    expect(tile(group, 'Accuracy')).toBe('Accuracy0.751share classified right');
    expect(group).toHaveTextContent(TEST_SCOPE);
    // No ranking or probability figure exists for test, and none is borrowed from validation.
    expect(group).not.toHaveTextContent(/PR-AUC|ROC-AUC|Brier|\bECE\b/);
    for (const figure of ['0.654', '0.841', '0.137', '0.031']) {
      expect(group).not.toHaveTextContent(figure);
    }
    // The record's churn rate and no-skill accuracy are not part of the release shown.
    expect(document.body).not.toHaveTextContent('0.261');
    expect(document.body).not.toHaveTextContent('0.750');
    expect(document.body).not.toHaveTextContent(/flagging nobody scores \d/);
    expect(screen.queryByRole('group', { name: 'Validation figures' })).toBeNull();
  });

  it('draws one square per test customer, as many of each outcome as the record counts', async () => {
    await showPerformance('/performance?part=test', RECORDED);
    const waffle = screen.getByRole('img', { name: /one square each/ });
    expect(waffle).toHaveAccessibleName(
      'Test customers at the cut 0.40, one square each: 250 caught, 100 missed, 250 offers to stayers, 800 left alone.',
    );
    const count = (outcome: string) =>
      waffle.querySelectorAll(`rect[data-outcome="${outcome}"]`).length;
    expect([count('tp'), count('fn'), count('fp'), count('tn')]).toEqual([250, 100, 250, 800]);
    expect(waffle.querySelectorAll('rect')).toHaveLength(recorded().metrics.n);
    const legend = card('Every test customer, by outcome');
    expect(legend).toHaveTextContent('Each square is one of the 1,400 test customers');
    for (const [name, value] of [
      ['caught', '250'],
      ['missed', '100'],
      ['offers to stayers', '250'],
      ['left alone', '800'],
    ] as const) {
      const told = within(legend).getByText(name, { selector: 'dt' }).nextElementSibling;
      expect(told?.textContent.startsWith(`${value} `)).toBe(true);
    }
  });

  it('says the record in plain words, from its own counts', async () => {
    await showPerformance('/performance?part=test', RECORDED);
    const plain = card('In plain words');
    expect(plain).toHaveTextContent('Of the 350 test customers who churned, 250 were flagged.');
    expect(plain).toHaveTextContent('recall 0.712');
    expect(plain).toHaveTextContent('Among the customers flagged, 250 churned and 250 stayed.');
    expect(plain).toHaveTextContent('precision 0.499');
    expect(plain).toHaveTextContent(
      '100 test customers who churned scored below the cut 0.40 and were not flagged.',
    );
    expect(within(plain).getByText('TEST')).toBeInTheDocument();
  });

  it('draws no bar for two empty cells, rather than one that looks full', async () => {
    const empty = recorded();
    await showPerformance(
      '/performance?part=test',
      performance({
        test: {
          ...empty,
          metrics: {
            ...empty.metrics,
            positives: 0,
            tp: 0,
            fn: 0,
            fp: 0,
            tn: 1400,
            precision: 0,
            recall: 0,
            f1: 0,
          },
        },
        test_reason: null,
      }),
    );
    const items = within(card('In plain words')).getAllByRole('listitem');
    expect(items.map((item) => item.querySelectorAll('svg').length)).toEqual([0, 0, 0]);
    expect(items[0]).toHaveTextContent('recall —');
    expect(items[1]).toHaveTextContent('precision —');
  });

  it('draws no square per customer when the cells do not add up to the customers', async () => {
    const odd = recorded();
    await showPerformance(
      '/performance?part=test',
      performance({ test: { ...odd, metrics: { ...odd.metrics, n: 1401 } }, test_reason: null }),
    );
    const waffle = card('Every test customer, by outcome');
    expect(within(waffle).queryByRole('img')).toBeNull();
    expect(waffle).toHaveTextContent('do not add up to its customers');
    expect(waffle).not.toHaveTextContent('Each square is one of');
    // The legend still states the record's own counts.
    expect(waffle).toHaveTextContent('800');
  });

  it('shows no test figure without a record, and says why', async () => {
    const reason = 'The recorded test evaluation is withheld under the disclosure policy.';
    await showPerformance(
      '/performance?part=test',
      performance({ test: null, test_reason: reason }),
    );
    const note = screen.getByRole('note', { name: 'Test evaluation' });
    expect(note).toHaveTextContent('No test figures shown');
    expect(note).toHaveTextContent(reason);
    expect(note).not.toHaveTextContent(/not recorded/i);
    expect(screen.queryByRole('group', { name: 'Test figures' })).toBeNull();
    expect(screen.queryByRole('img', { name: /one square each/ })).toBeNull();
    expect(within(card('How well it performs')).queryByText('TEST')).toBeNull();
    expect(card('How well it performs')).not.toHaveTextContent('what the recorded cut caught');
  });

  it('never puts a validation figure under a test label when there is no record', async () => {
    await showPerformance('/performance?part=test');
    const note = screen.getByRole('note', { name: 'Test evaluation' });
    expect(note).toHaveTextContent('No test evaluation is recorded for this model.');
    for (const figure of ['0.654', '0.841', '0.137', '0.031', '0.601', '0.743']) {
      expect(card('How well it performs')).not.toHaveTextContent(figure);
    }
  });
});

describe('the held-out test partition', () => {
  /** A record carrying fields the release does not publish, each a value found nowhere else. */
  const smuggled = {
    ...recorded(),
    metrics: {
      ...recorded().metrics,
      roc_auc: 0.9876,
      average_precision: 0.9654,
      brier: 0.0789,
      ece: 0.0567,
    },
    ece: 0.0543,
    brier: 0.0821,
    roc_auc: 0.9822,
    average_precision: 0.9733,
    optimal_threshold: 0.8765,
    reliability: [{ low: 0, high: 0.1, customers: 4321, mean_predicted: 0.0444, rate: 0.0456 }],
    segments: [{ dimension: 'Contract', segment: 'Month-to-month', customers: 8765 }],
    churn_rate: 0.3456,
    no_skill_accuracy: 0.6789,
  } as unknown as NonNullable<Performance['test']>;
  const SENTINELS = [
    ...['0.988', '0.9876', '0.965', '0.079', '0.057', '0.054', '0.082', '0.982', '0.973'],
    ...['0.877', '0.88', '4,321', '4321', '8,765', '8765', '0.346', '0.679', '0.046'],
  ];

  /** Every piece of text a reader or a screen reader meets, one node or label at a time. */
  function readable(): string[] {
    const out: string[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.nodeValue) out.push(node.nodeValue);
    }
    for (const element of document.body.querySelectorAll('[aria-label],[aria-valuetext],[title]')) {
      for (const name of ['aria-label', 'aria-valuetext', 'title']) {
        const value = element.getAttribute(name);
        if (value) out.push(value);
      }
    }
    return out;
  }

  function found(): string[] {
    const pieces = readable();
    return SENTINELS.filter((value) => pieces.some((piece) => piece.includes(value)));
  }

  it.each(EVERY_VIEW)('shows no unpublished test figure at %s', async (path) => {
    // The same view without the extra fields shows none of the values either,
    // so their absence below is the page's doing, not the fixture's.
    const plain = await showPerformance(path, RECORDED);
    expect(found()).toEqual([]);
    plain.unmount();
    await showPerformance(path, performance({ test: smuggled, test_reason: null }));
    expect(found()).toEqual([]);
    expect(document.body.textContent).not.toMatch(/test-optimal|optimal threshold/i);
  });

  it('labels every test figure TEST and every validation figure VALIDATION', async () => {
    await showPerformance('/performance?part=test', RECORDED);
    expect(
      within(screen.getByRole('group', { name: 'Test figures' })).getByText('TEST'),
    ).toBeVisible();
    for (const name of ['Every test customer, by outcome', 'In plain words']) {
      expect(within(card(name)).getByText('TEST')).toBeVisible();
    }
    expect(screen.queryByText('VALIDATION', { selector: '[role="group"] *' })).toBeNull();
  });
});

describe('errors and trade-offs: validation', () => {
  it('starts at the served cut, with the sweep point’s own figures and no two-by-two table', async () => {
    await showPerformance('/performance?tab=errors', RECORDED);
    const slider = screen.getByRole('slider', { name: 'Explored cut' });
    expect(slider).toHaveAttribute('aria-valuetext', '0.36');
    expect(screen.queryByRole('table', { name: /^Errors at / })).toBeNull();
    const explore = card('Explore a cut');
    expect(within(explore).getByText('VALIDATION')).toBeVisible();
    expect(strip('Flagged', explore)).toBe('512');
    expect(strip('Precision', explore)).toBe(at(36).precision.toFixed(3));
    expect(strip('Recall', explore)).toBe(at(36).recall.toFixed(3));
    expect(strip('F1', explore)).toBe(at(36).f1.toFixed(3));
    const readout = screen.getByText(/^At 0\.36: 512 flagged · precision /);
    expect(readout).toHaveAttribute('aria-live', 'polite');
    // A validation cut is read as its rates: no cell of a validation table is told.
    expect(explore).not.toHaveTextContent(/stayed|churned|caught|missed|left alone/);
    expect(explore).not.toHaveTextContent('$50');
  });

  it('moves with the fader, replacing the address rather than adding to the history', async () => {
    await showPerformance('/performance?tab=errors');
    const added = entriesAdded();
    fireEvent.change(screen.getByRole('slider', { name: 'Explored cut' }), {
      target: { value: '50' },
    });
    expect(strip('Flagged')).toBe(String(at(50).flagged));
    expect(screen.getByText(/^At 0\.50: /)).toHaveAttribute('aria-live', 'polite');
    expect(search('cut')).toBe('0.50');
    expect(added()).toBe(0);
    // Back at the served cut, the address is clean again.
    fireEvent.change(screen.getByRole('slider', { name: 'Explored cut' }), {
      target: { value: '36' },
    });
    expect(search('cut')).toBeNull();
  });

  it('goes back to the served cut with the fader’s reset', async () => {
    await showPerformance('/performance?tab=errors&cut=0.50');
    await userEvent.click(screen.getByRole('button', { name: /reset/i }));
    expect(search('cut')).toBeNull();
    expect(screen.getByText(/^At 0\.36: /)).toBeInTheDocument();
  });

  it.each([
    ['/performance?tab=errors&cut=0.50', /^At 0\.50: /],
    ['/performance?tab=errors&cut=0.00', /^At 0\.00: /],
    // Not a cut as the grid writes it: the served cut.
    ['/performance?tab=errors&cut=0.5', /^At 0\.36: /],
  ])('opens %s at the cut from the address, or the served one', async (path, readout) => {
    await showPerformance(path);
    expect(screen.getByText(readout)).toBeInTheDocument();
  });

  it('falls back to the served cut for a cut the curve lacks, and leaves the address alone', async () => {
    const lacking = curve();
    await showPerformance(
      '/performance?tab=errors&cut=0.50',
      performance({
        validation: { ...lacking, points: lacking.points.filter((_, i) => i !== 50) },
      }),
    );
    expect(screen.getByText(/^At 0\.36: /)).toBeInTheDocument();
    expect(search('cut')).toBe('0.50');
  });

  it('keeps the cut in the address while the figures load', async () => {
    const slow = held('/api/performance', performance());
    server.use(slow.handler);
    renderAt(<App />, '/performance?tab=errors&cut=0.50');
    await screen.findByText('Loading the model’s evaluation');
    expect(search('cut')).toBe('0.50');
    slow.release();
    expect(await screen.findByText(/^At 0\.50: /)).toBeInTheDocument();
  });

  it('writes "—" for precision and F1 at a cut that flags nobody, and 0 where the flagged stayed', async () => {
    const first = await showPerformance('/performance?tab=errors&cut=1.00');
    expect(at(100).flagged).toBe(0);
    expect([strip('Precision'), strip('Recall'), strip('F1')]).toEqual(['—', '0.000', '—']);
    first.unmount();
    await showPerformance('/performance?tab=errors&cut=0.97');
    expect(at(97).flagged).toBe(3);
    expect([strip('Precision'), strip('Recall'), strip('F1')]).toEqual(['0.000', '0.000', '0.000']);
  });

  it('quotes how the served cut was set, and says it is one cut among others', async () => {
    await showPerformance('/performance?tab=errors');
    const errors = card('Explore a cut');
    expect(errors).toHaveTextContent(modelInfo().threshold_source);
    expect(errors).toHaveTextContent('not a universal optimum');
    expect(within(errors).getByRole('link', { name: /simulator/i })).toHaveAttribute(
      'href',
      '/simulator',
    );
  });
});

describe('errors and trade-offs: test', () => {
  it('shows the record’s two-by-two table at its own cut, without a fader or a curve', async () => {
    await showPerformance('/performance?tab=errors&part=test', RECORDED);
    expect(errorsTable()).toHaveAccessibleName('Errors at 0.40, test record');
    expect(matrix()).toEqual({ tn: '800', fp: '250', fn: '100', tp: '250' });
    const table = errorsTable();
    expect(within(table).getByRole('cell', { name: /^250 caught/ })).toBeInTheDocument();
    expect(within(table).getByRole('cell', { name: /^100 missed/ })).toBeInTheDocument();
    expect(within(table).getByRole('cell', { name: /^800 left alone/ })).toBeInTheDocument();
    expect(within(table).getByRole('cell', { name: /^250 offers to stayers/ })).toHaveTextContent(
      '$50 each if offered, assumed',
    );
    const errors = card('Errors at the recorded cut');
    expect([
      strip('Precision', errors),
      strip('Recall', errors),
      strip('F1', errors),
      strip('Accuracy', errors),
    ]).toEqual(['0.499', '0.712', '0.587', '0.751']);
    expect(within(errors).queryByText('Flagged', { selector: 'dt' })).toBeNull();
    expect(
      screen.getByText('At 0.40: 250 churners caught, 100 missed; 250 offers to stayers.'),
    ).toBeInTheDocument();
    expect(within(errors).getByText('TEST')).toBeVisible();
    expect(screen.queryByRole('slider')).toBeNull();
    expect(screen.queryByRole('region', { name: 'Precision against recall' })).toBeNull();
    expect(card('What the test record holds')).toHaveTextContent(
      'The test release is recorded at one cut only',
    );
  });

  it('reads recall as 0, not undefined, for a record whose cut reached no churner', async () => {
    const none = recorded();
    await showPerformance(
      '/performance?tab=errors&part=test',
      performance({
        test: {
          ...none,
          metrics: { ...none.metrics, tp: 0, fp: 10, fn: 350, precision: 0, recall: 0, f1: 0 },
        },
        test_reason: null,
      }),
    );
    expect([strip('Precision'), strip('Recall'), strip('F1')]).toEqual(['0.000', '0.000', '0.000']);
  });

  it('shows no table without a record, and says why', async () => {
    await showPerformance('/performance?tab=errors&part=test');
    expect(screen.queryByRole('table', { name: /^Errors at / })).toBeNull();
    expect(screen.getByRole('note', { name: 'Test evaluation' })).toHaveTextContent(
      'No test figures shown',
    );
    expect(screen.queryByRole('slider')).toBeNull();
  });
});

describe('precision against recall', () => {
  it('tables each cut’s served figures by column, "—" where nothing is flagged', async () => {
    await showPerformance('/performance?tab=errors');
    const p35 = at(35);
    expect(chartRow(prTable(), '0.35')).toEqual([
      String(p35.flagged),
      p35.precision.toFixed(3),
      p35.recall.toFixed(3),
      p35.f1.toFixed(3),
      '',
    ]);
    expect(chartRow(prTable(), '1.00')).toEqual(['0', '—', '0.000', '—', '']);
    expect(chartRow(prTable(), '0.36')[4]).toBe('served');
  });

  it('marks and names the explored cut beside the served one', async () => {
    await showPerformance('/performance?tab=errors&cut=0.50');
    expect(chartRow(prTable(), '0.50')[4]).toBe('explored');
    expect(chartRow(prTable(), '0.36')[4]).toBe('served');
    expect(
      screen.getByRole('img', { name: /the served cut 0\.36 has .*; the explored cut 0\.50 has / }),
    ).toBeInTheDocument();
  });

  it('draws the no-skill line at the precision of flagging everyone, as served', async () => {
    await showPerformance('/performance?tab=errors');
    expect(card('Precision against recall')).toHaveTextContent('no skill: the churn rate, 0.260');
  });

  it('loads a clicked cut into the fader, adding one history entry', async () => {
    await showPerformance('/performance?tab=errors');
    const added = entriesAdded();
    pick(50);
    expect(screen.getByText(/^At 0\.50: /)).toBeInTheDocument();
    expect(screen.getByRole('slider', { name: 'Explored cut' })).toHaveAttribute(
      'aria-valuetext',
      '0.50',
    );
    expect(added()).toBe(1);
  });

  it('loads the first cut too', async () => {
    await showPerformance('/performance?tab=errors');
    pick(0);
    expect(screen.getByText(/^At 0\.00: /)).toBeInTheDocument();
  });

  it('keeps the pointer layer out of the keyboard’s way', async () => {
    await showPerformance('/performance?tab=errors');
    const capture = screen.getByTestId('pr-capture');
    expect(capture).toHaveAttribute('aria-hidden', 'true');
    expect(capture).not.toHaveAttribute('tabindex');
  });
});

describe('segments', () => {
  function segmentTable(name: RegExp = /^Segments at 0\.36, validation$/) {
    return screen.getByRole('table', { name });
  }

  it('reads each rate with its interval, and names undefined and small groups for what they are', async () => {
    await showPerformance('/performance?tab=calibration');
    const table = segmentTable();
    expect(cells(table, 'Month-to-month')).toEqual([
      '690',
      '281',
      '40.1%',
      '352',
      '0.553 (0.481–0.631)',
      '0.701 (0.012–0.891)',
      '0.712',
    ]);
    expect(within(table).getByRole('rowheader', { name: /Two year/ })).toHaveTextContent(
      'NOT SCANNED',
    );
    expect(cells(table, /Two year/)[4]).toBe('—');
    // Nobody flagged, nobody churned: undefined both ways, and not "not scanned".
    expect(cells(table, 'No internet')).toEqual(['290', '0', '40.1%', '0', '—', '—', '0.712']);
    // One bound missing is no interval; no ranking within is "—".
    expect(cells(table, 'No')).toEqual([
      '985',
      '220',
      '40.1%',
      '230',
      '0.551',
      '0.663 (0.012–0.891)',
      '—',
    ]);
    expect(cells(table, 'Female')).toEqual(['12', 'fewer than 20 customers', '5', '—', '—', '—']);
    expect(within(table).getByRole('rowheader', { name: 'Female' })).not.toHaveTextContent(
      'NOT SCANNED',
    );
    expect(table).toHaveTextContent('Senior citizen');
    expect(table).toHaveTextContent('Gender');
    expect(screen.getByRole('region', { name: 'Segment table' })).toHaveAttribute('tabindex', '0');
    const segments = card('Performance by segment at 0.36');
    expect(within(segments).getByText('VALIDATION')).toBeVisible();
    expect(segments).toHaveTextContent(UNSCANNED);
    expect(segments).toHaveTextContent(GENDER);
  });

  it('draws each served rate on a small bar, and none where the rate is undefined', async () => {
    await showPerformance('/performance?tab=calibration');
    const table = segmentTable();
    const row = within(table).getByRole('rowheader', { name: 'Month-to-month' }).closest('tr');
    const width = (rate: string) =>
      row?.querySelector(`svg[data-rate="${rate}"] rect:last-child`)?.getAttribute('width');
    expect(Number(width('precision'))).toBeCloseTo(55.33);
    expect(Number(width('recall'))).toBeCloseTo(70.11);
    const empty = within(table).getByRole('rowheader', { name: 'No internet' }).closest('tr');
    expect(empty?.querySelectorAll('svg')).toHaveLength(0);
  });

  it('offers no test breakdown, whatever is recorded', async () => {
    await showPerformance('/performance?tab=calibration&part=test', RECORDED);
    const segments = card('Performance by segment at 0.36');
    expect(within(segments).queryByRole('group', { name: /Partition/ })).toBeNull();
    expect(within(segments).queryByText('TEST')).toBeNull();
    expect(segmentTable(/^Segments at 0\.36, validation$/)).toBeInTheDocument();
  });
});

describe('calibration', () => {
  it('tables the served reliability bins, and leaves a small bin without a rate', async () => {
    await showPerformance('/performance?tab=calibration');
    const table = screen.getByRole('table', {
      name: 'Observed churn by probability bin, validation',
    });
    const bins = measured().reliability;
    expect(chartRow(table, '0.0–0.1')).toEqual(['412', '0.043', '0.051']);
    expect(chartRow(table, '0.8–0.9')).toEqual([
      String(bins[4]?.customers),
      '0.823',
      'fewer than 20 customers',
    ]);
    expect(
      screen.getByRole('img', {
        name: 'Observed churn against mean stated probability in 4 validation bins; 1 with too few customers is not drawn.',
      }),
    ).toBeInTheDocument();
  });

  it('sizes each drawn bin by its customers, the largest largest', async () => {
    await showPerformance('/performance?tab=calibration');
    const chart = screen.getByRole('img', { name: /validation bins/ });
    const radii = [...chart.querySelectorAll('circle')].map((c) => Number(c.getAttribute('r')));
    // 412, 233, 198 and 160 customers, in the bins' order.
    expect(radii).toHaveLength(4);
    expect(radii[0]).toBe(9);
    expect([...radii].sort((a, b) => b - a)).toEqual(radii);
  });

  it('puts the served ECE in the header, and says which bins are too small to draw', async () => {
    await showPerformance('/performance?tab=calibration');
    const calibration = card('Does a probability mean what it says?');
    expect(within(calibration).getByText('ECE 0.031')).toBeVisible();
    expect(within(calibration).getByText('VALIDATION')).toBeVisible();
    expect(calibration).toHaveTextContent(
      'Not drawn: 0.8–0.9 (14 customers), fewer than 20 customers.',
    );
    expect(calibration).toHaveTextContent(/ECE 0\.031\. /);
  });

  it('draws no bin, and says so, when none is served', async () => {
    await showPerformance(
      '/performance?tab=calibration',
      performance({ validation_evaluation: measured({ reliability: [] }) }),
    );
    expect(
      screen.getByRole('img', {
        name: 'Observed churn against mean stated probability in 0 validation bins.',
      }),
    ).toBeInTheDocument();
  });

  it('compares the uncalibrated and served model at 0.50 from the record', async () => {
    await showPerformance('/performance?tab=calibration');
    const table = screen.getByRole('table', { name: 'At a cut of 0.50' });
    expect(cells(table, 'Precision')).toEqual(['0.501', '0.661']);
    expect(cells(table, 'Recall')).toEqual(['0.778', '0.522']);
    expect(cells(table, 'Brier')).toEqual(['0.167', '0.139']);
    expect(
      screen.getByText(
        /Isotonic calibration was kept because validation ECE on raw probabilities fell from 0\.144 to 0\.028/,
      ),
    ).toBeInTheDocument();
  });

  it('writes "—" where training recorded no figures, and says the model is not calibrated', async () => {
    await showPerformance(
      '/performance?tab=calibration',
      performance({
        calibration: {
          method: null,
          epsilon: null,
          uncalibrated: null,
          calibrated: null,
          uncalibrated_ece: null,
          calibrated_ece: null,
        },
      }),
    );
    const table = screen.getByRole('table', { name: 'At a cut of 0.50' });
    expect(cells(table, 'Precision')).toEqual(['—', '—']);
    expect(screen.getByText('The served probabilities are not calibrated.')).toBeInTheDocument();
  });

  it('keeps the training records when validation is withheld', async () => {
    await showPerformance(
      '/performance?tab=calibration',
      performance({ validation: null, validation_evaluation: null, withheld_reason: 'x' }),
    );
    expect(screen.getByRole('table', { name: 'At a cut of 0.50' })).toBeInTheDocument();
  });
});

describe('model selection', () => {
  const CHOICE = '/performance?tab=choice';

  it('charts the candidates inside one standard error, and tables all of them', async () => {
    await showPerformance(CHOICE);
    const chart = screen.getByRole('table', { name: /Cross-validated PR-AUC of the candidates/ });
    expect(within(chart).getAllByRole('row')).toHaveLength(1 + 3);
    expect(chartRow(chart, 'Random forest')).toEqual(['0.6704', '0.0071', 'leader']);
    expect(chartRow(chart, 'Logistic regression')).toEqual(['0.6652', '0.0104', 'selected']);
    const table = screen.getByRole('table', { name: 'Candidate scores, uncalibrated' });
    expect(cells(table, 'Baseline (prior)')).toEqual(['0.0004', '0.2622', '0.5000']);
    expect(screen.getByText('cutoff 0.6633 = 0.6704 − 0.0071')).toBeInTheDocument();
    expect(card('Model selection: the one-standard-error rule')).toHaveTextContent(
      'Random forest leads Logistic regression by 0.0052, inside one standard error, so the simpler model is chosen.',
    );
  });

  it('draws each whisker from the served score and error, as places along the plot', async () => {
    await showPerformance(CHOICE);
    const plots = card('Model selection: the one-standard-error rule').querySelectorAll('svg');
    const percent = (value: string | null) => Number(value?.replace('%', ''));
    for (const plot of [...plots].slice(0, 3)) {
      const [cutoff, whisker] = plot.querySelectorAll('line');
      const dot = plot.querySelector('circle');
      const low = percent(whisker?.getAttribute('x1') ?? null);
      const high = percent(whisker?.getAttribute('x2') ?? null);
      const centre = percent(dot?.getAttribute('cx') ?? null);
      expect(low).toBeLessThan(centre);
      expect(centre).toBeLessThan(high);
      // The interval is symmetric: the server's score, plus and minus its error.
      expect(centre - low).toBeCloseTo(high - centre, 1);
      expect(cutoff?.getAttribute('x1')).toBe(cutoff?.getAttribute('x2'));
    }
  });

  it('states the lead the server gives, and works none out', async () => {
    const figures = performance();
    await showPerformance(
      CHOICE,
      performance({ selection: figures.selection && { ...figures.selection, leader_gap: 0.1234 } }),
    );
    expect(card('Model selection: the one-standard-error rule')).toHaveTextContent(
      'Random forest leads Logistic regression by 0.1234,',
    );
  });

  it('links to the model card, and to no report holding figures the policy does not publish', async () => {
    await showPerformance(CHOICE, RECORDED);
    expect(screen.getByRole('link', { name: /Model card/ })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Final evaluation/ })).toBeNull();
  });

  it('marks the leader the server names, not the highest score', async () => {
    const figures = performance();
    await showPerformance(
      CHOICE,
      performance({
        selection: figures.selection && {
          ...figures.selection,
          leader_name: 'Hist Gradient Boosting',
        },
      }),
    );
    const chart = screen.getByRole('table', { name: /Cross-validated PR-AUC of the candidates/ });
    expect(chartRow(chart, 'Hist. gradient boosting')[2]).toBe('leader');
    expect(chartRow(chart, 'Random forest')[2]).toBe('');
  });

  it('marks no leader, and claims no sum, for a leader the comparison does not list', async () => {
    const figures = performance();
    await showPerformance(
      CHOICE,
      performance({
        selection: figures.selection && { ...figures.selection, leader_name: 'Gone' },
      }),
    );
    expect(screen.getByText('cutoff 0.6633')).toBeInTheDocument();
    const chart = screen.getByRole('table', { name: /Cross-validated PR-AUC of the candidates/ });
    expect(chart).not.toHaveTextContent('leader');
    expect(card('Model selection: the one-standard-error rule')).toHaveTextContent(
      'The simplest model inside the cutoff is chosen.',
    );
  });

  it('writes the cutoff as the leader less its error only when the figures agree', async () => {
    const figures = performance();
    await showPerformance(
      CHOICE,
      performance({ selection: figures.selection && { ...figures.selection, cutoff: 0.6501 } }),
    );
    expect(screen.getByText('cutoff 0.6501')).toBeInTheDocument();
  });

  it.each([
    ['the comparison', { candidates: null }],
    ['the selection', { selection: null }],
  ])('says the comparison is not recorded when %s is missing', async (_, missing) => {
    await showPerformance(CHOICE, performance(missing));
    expect(card('Model selection: the one-standard-error rule')).toHaveTextContent(
      'The model comparison is not recorded in this artifact’s reports.',
    );
  });
});

describe('the words on the page', () => {
  it.each(EVERY_VIEW)('claim no cause, verdict or ratio at %s', async (path) => {
    const first = await showPerformance(path);
    expectNoClaims();
    expect(document.body).toHaveTextContent('This page computes nothing from the test partition');
    first.unmount();
    await showPerformance(path, RECORDED);
    expectNoClaims();
  });
});

describe('the sidebar', () => {
  it("reads the page's own figures: one request for both, kept across tabs, partitions and pages", async () => {
    const seen = recordRequests();
    await showPerformance(
      '/performance',
      performance({
        test: recorded(),
        test_reason: null,
        validation_evaluation: measured({ ece: 0.0412 }),
      }),
    );
    const ece = screen.getByText('Validation ECE').nextElementSibling;
    expect(ece).toHaveTextContent(/^0\.041$/);
    for (const name of [/Model choice/, /Errors & trade-offs/, /Calibration/, /Summary/]) {
      await userEvent.click(screen.getByRole('tab', { name }));
    }
    await userEvent.click(within(partition()).getByRole('button', { name: 'Test' }));
    act(() => window.history.back());
    act(() => navigate('/batch'));
    act(() => navigate('/performance'));
    await screen.findByRole('tablist', { name: 'Performance sections' });
    expect(seen.filter((url) => url.pathname === '/api/performance')).toHaveLength(1);
  });
});
