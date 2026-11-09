import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw/http';
import { App } from '../../app/App';
import { MAIN_ID } from '../../components/shell/Shell';
import { modelInfo } from '../../test/fixtures';
import { entriesAdded } from '../../test/history';
import { curve, measured, performance, performanceApi, recorded } from '../../test/performanceApi';
import { renderAt } from '../../test/render';
import { answer, refuse, server } from '../../test/server';
import { FAILED, GENDER, MISMATCH, TEST_SCOPE, TITLE, UNAVAILABLE, UNSCANNED } from './copy';
import { PR, prX, prY } from './performance';

/** The page at ``path``, with ``body`` as the performance response. */
async function showPerformance(path = '/performance', body = performance()) {
  server.use(performanceApi(body));
  const rendered = renderAt(<App />, path);
  await screen.findByRole('heading', { level: 1, name: TITLE });
  await screen.findByRole('table', { name: 'Metrics by partition' });
  return rendered;
}

const RECORDED = performance({ test: recorded(), test_reason: null });

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

function scorecard() {
  return screen.getByRole('table', { name: 'Metrics by partition' });
}

function card(name: string) {
  return screen.getByRole('region', { name });
}

/** The value a term names in the errors card's summary strip. */
function strip(term: string): string {
  return (
    within(card('Errors at a cut')).getByText(term, { selector: 'dt' }).nextElementSibling
      ?.textContent ?? ''
  );
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

/** A click on the precision–recall picture at point ``i`` of the fixture curve. */
function pick(i: number) {
  const capture = screen.getByTestId('pr-capture');
  vi.spyOn(capture, 'getBoundingClientRect').mockReturnValue(
    DOMRect.fromRect({ x: 0, y: 0, width: PR.width, height: PR.height }),
  );
  fireEvent.click(capture, { clientX: prX(at(i).recall), clientY: prY(at(i).precision) });
}

/** Every word on the page: none claims a cause, a rescue or a verdict. */
function expectNoClaims() {
  const text = document.body.textContent;
  expect(text).not.toMatch(
    /\b(causes|caused by|causing|drives|driven by|driving|will prevent|prevents|guarantee[sd]?)\b/i,
  );
  expect(text).not.toMatch(/\b(saved?|saves|rescued?|prevented|excellent)\b/i);
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

describe('loading and failure', () => {
  it('says it is loading until the evaluation arrives', async () => {
    const slow = held('/api/performance', performance());
    server.use(slow.handler);
    renderAt(<App />, '/performance');
    expect(await screen.findByText('Loading the model’s evaluation')).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'Metrics by partition' })).not.toBeInTheDocument();
    slow.release();
    expect(await screen.findByRole('table', { name: 'Metrics by partition' })).toBeInTheDocument();
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
    expect(screen.queryByRole('table', { name: 'Metrics by partition' })).toBeNull();
    slow.release();
    expect(await screen.findByRole('table', { name: 'Metrics by partition' })).toBeInTheDocument();
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
    expect(await screen.findByRole('table', { name: 'Metrics by partition' })).toBeInTheDocument();
  });

  it('fails the same way when the model’s description cannot be had', async () => {
    server.use(performanceApi(), refuse('get', '/api/model-info', 500, 'internal_error'));
    renderAt(<App />, '/performance');
    expect(await screen.findByText(FAILED)).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'Metrics by partition' })).not.toBeInTheDocument();
  });

  it('shows no figure when the evaluation describes another artifact than the model', async () => {
    server.use(performanceApi(performance({ model_sha256: 'b'.repeat(64) })));
    renderAt(<App />, '/performance');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(MISMATCH);
    expect(screen.queryByRole('table', { name: 'Metrics by partition' })).not.toBeInTheDocument();
    server.use(performanceApi());
    await userEvent.click(within(alert).getByRole('button', { name: 'Reload figures' }));
    expect(await screen.findByRole('table', { name: 'Metrics by partition' })).toBeInTheDocument();
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
      expect(
        await screen.findByRole('table', { name: 'Metrics by partition' }),
      ).toBeInTheDocument();
      expect(screen.queryByText(MISMATCH)).not.toBeInTheDocument();
    },
  );
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
    await showPerformance('/performance', RECORDED);
    const evidence = card('Where each figure comes from');
    expect(evidence).toHaveTextContent('Recorded 2026-10-21');
    expect(evidence).toHaveTextContent('a re-checked estimate, not an independent one');
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

describe('the scorecard', () => {
  it('reads each validation figure from its own field, with counts behind the rates', async () => {
    await showPerformance();
    const table = scorecard();
    expect(cells(table, /^PR-AUC \(average precision\)/)).toEqual([
      expect.stringMatching(/^0\.654.*flagging at random scores 0\.271$/),
    ]);
    expect(cells(table, /^ROC-AUC/)).toEqual(['0.841']);
    expect(cells(table, /^Recall/)).toEqual(['0.743222 of 310 churners flagged']);
    expect(cells(table, /^Precision/)).toEqual(['0.601222 of 385 flagged churned']);
    expect(cells(table, /^F1/)).toEqual(['0.654']);
    expect(cells(table, /^Brier/)).toEqual(['0.137']);
    expect(cells(table, /^ECE/)).toEqual(['0.031']);
    expect(cells(table, /^Accuracy/)).toEqual(['0.812flagging nobody scores 0.733']);
    expect(within(table).getByRole('columnheader', { name: /Validation/ })).toHaveTextContent(
      '1,250',
    );
    expect(within(table).getByRole('rowgroup', { name: 'At the cut 0.36' })).toBeInTheDocument();
  });

  it('has no test column without a record, and never puts validation under a test label', async () => {
    await showPerformance();
    expect(within(scorecard()).queryByRole('columnheader', { name: /Test/ })).toBeNull();
    const note = screen.getByRole('note', { name: 'Test evaluation' });
    expect(note).toHaveTextContent('No test figures shown');
    expect(note).toHaveTextContent('No test evaluation is recorded for this model.');
  });

  it('adds the recorded test column beside validation, every figure its own', async () => {
    await showPerformance('/performance', RECORDED);
    const table = scorecard();
    expect(within(table).getByRole('columnheader', { name: /Test · recorded/ })).toHaveTextContent(
      '1,400',
    );
    const test = (row: RegExp) => cells(table, row)[1];
    expect(test(/^PR-AUC/)).toBe('—not published for test');
    expect(test(/^ROC-AUC/)).toBe('—not published for test');
    expect(test(/^Recall/)).toBe('0.712250 of 350 churners flagged');
    expect(test(/^Precision/)).toBe('0.499250 of 500 flagged churned');
    expect(test(/^F1/)).toBe('0.587');
    expect(test(/^Brier/)).toBe('—not published for test');
    expect(test(/^ECE/)).toBe('—not published for test');
    expect(test(/^Accuracy/)).toBe('0.751flagging nobody scores 0.750');
    expect(within(table).getByRole('rowgroup', { name: 'At the cut 0.36' })).toBeInTheDocument();
    expect(screen.getByText(TEST_SCOPE)).toBeInTheDocument();
    expect(screen.queryByText(/test-optimal/)).toBeNull();
    expect(screen.queryByRole('note', { name: 'Test evaluation' })).toBeNull();
  });

  it('never fills a withheld test figure from validation, and validation keeps its own', async () => {
    await showPerformance('/performance', RECORDED);
    const table = scorecard();
    const [validation, test] = [0, 1].map((i) => (row: RegExp) => cells(table, row)[i]);
    expect(validation?.(/^ROC-AUC/)).toBe('0.841');
    expect(validation?.(/^Brier/)).toBe('0.137');
    expect(validation?.(/^PR-AUC/)).toMatch(/^0\.654/);
    for (const row of [/^PR-AUC/, /^ROC-AUC/, /^Brier/, /^ECE/]) {
      expect(test?.(row)).toBe('—not published for test');
    }
    expect(test?.(/^Recall/)).toBe('0.712250 of 350 churners flagged');
  });

  it('says the same whatever kept a recorded evaluation from being shown', async () => {
    const reason = 'The recorded test evaluation is withheld under the disclosure policy.';
    await showPerformance('/performance', performance({ test: null, test_reason: reason }));
    const note = screen.getByRole('note', { name: 'Test evaluation' });
    expect(note).toHaveTextContent('No test figures shown');
    expect(note).toHaveTextContent(reason);
    expect(note).not.toHaveTextContent(/not recorded/i);
    expect(card('Where each figure comes from')).not.toHaveTextContent('Not recorded');
  });

  it('says validation is withheld, with the reason, wherever a validation figure would be', async () => {
    const reason = 'the split differs from the one the model was trained on';
    await showPerformance(
      '/performance',
      performance({ validation: null, validation_evaluation: null, withheld_reason: reason }),
    );
    const said = `Validation outcomes are withheld: ${reason}`;
    expect(scorecard()).toHaveTextContent(said);
    expect(within(card('Where each figure comes from')).getByRole('note')).toHaveTextContent(said);
    for (const name of [
      'Errors at a cut',
      'Precision against recall',
      'Does one cut serve every segment?',
      'Does a probability mean what it says?',
    ])
      expect(card(name)).toHaveTextContent(said);
    // The training records do not rest on the book, so they still show.
    expect(screen.getByRole('table', { name: 'At a cut of 0.50' })).toBeInTheDocument();
    expect(
      screen.getByRole('table', { name: 'Candidate scores, uncalibrated' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('slider')).toBeNull();
  });

  it('says validation is unavailable, without inventing a reason, when nothing is served', async () => {
    await showPerformance(
      '/performance',
      performance({ validation: null, validation_evaluation: null }),
    );
    expect(cells(scorecard(), /^Recall/)).toEqual(['—']);
    expect(scorecard()).toHaveTextContent(UNAVAILABLE);
    expect(document.body).not.toHaveTextContent('withheld');
    for (const name of [
      'Errors at a cut',
      'Precision against recall',
      'Does one cut serve every segment?',
    ])
      expect(card(name)).toHaveTextContent(UNAVAILABLE);
    expect(screen.queryByRole('slider')).toBeNull();
  });

  it('says the measured figures are unavailable when only the sweep is served', async () => {
    await showPerformance('/performance', performance({ validation_evaluation: null }));
    expect(scorecard()).toHaveTextContent(UNAVAILABLE);
    // The sweep is a separate figure and still shows.
    expect(screen.getByRole('slider', { name: 'Explored cut' })).toBeInTheDocument();
    expect(card('Does one cut serve every segment?')).toHaveTextContent(UNAVAILABLE);
  });
});

describe('errors at a cut', () => {
  it('starts at the served cut, with the sweep’s own counts there', async () => {
    await showPerformance();
    const slider = screen.getByRole('slider', { name: 'Explored cut' });
    expect(slider).toHaveAttribute('aria-valuetext', 'threshold 0.36');
    expect(errorsTable()).toHaveAccessibleName('Errors at 0.36');
    // 701 is the served figure; customers less churners less offers would be 724.
    expect(matrix()).toEqual({ tn: '701', fp: '216', fn: '14', tp: '296' });
    expect(
      within(errorsTable()).getByRole('cell', { name: /(?<!not )flagged, stayed/ }),
    ).toHaveTextContent('$50 each if offered, assumed');
    expect(strip('Flagged')).toBe('512');
    expect(strip('Precision')).toBe(at(36).precision.toFixed(3));
    expect(strip('Recall')).toBe(at(36).recall.toFixed(3));
    expect(strip('F1')).toBe(at(36).f1.toFixed(3));
    const summary = screen.getByText(
      'At 0.36: 512 flagged; 216 of them stayed, 14 churners not flagged.',
    );
    expect(summary).toHaveAttribute('aria-live', 'polite');
  });

  it('moves with the slider, replacing the address rather than adding to the history', async () => {
    await showPerformance();
    const added = entriesAdded();
    fireEvent.change(screen.getByRole('slider', { name: 'Explored cut' }), {
      target: { value: '50' },
    });
    expect(errorsTable()).toHaveAccessibleName('Errors at 0.50');
    expect(strip('Flagged')).toBe(String(at(50).flagged));
    expect(screen.getByText(/^At 0\.50: /)).toHaveAttribute('aria-live', 'polite');
    expect(new URLSearchParams(window.location.search).get('cut')).toBe('0.50');
    expect(added()).toBe(0);
    // Back at the served cut, the address is clean again.
    fireEvent.change(screen.getByRole('slider', { name: 'Explored cut' }), {
      target: { value: '36' },
    });
    expect(new URLSearchParams(window.location.search).get('cut')).toBeNull();
  });

  it.each([
    ['/performance?cut=0.50', 'Errors at 0.50'],
    ['/performance?cut=0.00', 'Errors at 0.00'],
    // Not a cut as the grid writes it: the served cut.
    ['/performance?cut=0.5', 'Errors at 0.36'],
  ])('opens %s at the cut from the address, or the served one', async (path, name) => {
    await showPerformance(path);
    expect(errorsTable()).toHaveAccessibleName(name);
  });

  it('falls back to the served cut for a cut the curve lacks, and leaves the address alone', async () => {
    const lacking = curve();
    await showPerformance(
      '/performance?cut=0.50',
      performance({
        validation: { ...lacking, points: lacking.points.filter((_, i) => i !== 50) },
      }),
    );
    expect(errorsTable()).toHaveAccessibleName('Errors at 0.36');
    expect(new URLSearchParams(window.location.search).get('cut')).toBe('0.50');
  });

  it('keeps the cut in the address while the figures load', async () => {
    const slow = held('/api/performance', performance());
    server.use(slow.handler);
    renderAt(<App />, '/performance?cut=0.50');
    await screen.findByText('Loading the model’s evaluation');
    expect(new URLSearchParams(window.location.search).get('cut')).toBe('0.50');
    slow.release();
    expect(await screen.findByRole('table', { name: 'Errors at 0.50' })).toBeInTheDocument();
  });

  it('writes "—" for precision and F1 at a cut that flags nobody, and 0 where the flagged stayed', async () => {
    const first = await showPerformance('/performance?cut=1.00');
    expect(at(100).flagged).toBe(0);
    expect([strip('Precision'), strip('Recall'), strip('F1')]).toEqual(['—', '0.000', '—']);
    first.unmount();
    await showPerformance('/performance?cut=0.97');
    expect(at(97).flagged).toBe(3);
    expect([strip('Precision'), strip('Recall'), strip('F1')]).toEqual(['0.000', '0.000', '0.000']);
  });

  it('shows the recorded test counts under their own tab at the record’s cut, without a slider', async () => {
    await showPerformance('/performance', RECORDED);
    expect(screen.getByRole('tablist', { name: 'Partition for the errors' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: 'Test · recorded at 0.40' }));
    expect(errorsTable()).toHaveAccessibleName('Errors at 0.40');
    expect(matrix()).toEqual({ tn: '800', fp: '250', fn: '100', tp: '250' });
    expect([strip('Flagged'), strip('Precision'), strip('Recall'), strip('F1')]).toEqual([
      '500',
      '0.499',
      '0.712',
      '0.587',
    ]);
    expect(
      screen.getByText('At 0.40: 500 flagged; 250 of them stayed, 100 churners not flagged.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('slider')).toBeNull();
  });

  it('reads recall as 0, not undefined, for a record whose cut reached no churner', async () => {
    const none = recorded();
    await showPerformance(
      '/performance',
      performance({
        test: {
          ...none,
          metrics: { ...none.metrics, tp: 0, fp: 10, fn: 350, precision: 0, recall: 0, f1: 0 },
        },
        test_reason: null,
      }),
    );
    await userEvent.click(screen.getByRole('tab', { name: 'Test · recorded at 0.40' }));
    expect([strip('Precision'), strip('Recall'), strip('F1')]).toEqual(['0.000', '0.000', '0.000']);
  });

  it('goes back to validation when a cut is picked on the chart from the test tab', async () => {
    await showPerformance('/performance', RECORDED);
    await userEvent.click(screen.getByRole('tab', { name: 'Test · recorded at 0.40' }));
    pick(50);
    expect(screen.getByRole('tab', { name: 'Validation · explore' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(errorsTable()).toHaveAccessibleName('Errors at 0.50');
  });

  it('quotes how the served cut was set, and says it is one cut among others', async () => {
    await showPerformance();
    const errors = card('Errors at a cut');
    expect(errors).toHaveTextContent(modelInfo().threshold_source);
    expect(errors).toHaveTextContent('not a universal optimum');
    expect(within(errors).getByRole('link', { name: /simulator/i })).toHaveAttribute(
      'href',
      '/simulator',
    );
  });
});

describe('precision against recall', () => {
  it('tables each cut’s served figures by column, "—" where nothing is flagged', async () => {
    await showPerformance();
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
    await showPerformance('/performance?cut=0.50');
    expect(chartRow(prTable(), '0.50')[4]).toBe('explored');
    expect(chartRow(prTable(), '0.36')[4]).toBe('served');
    expect(
      screen.getByRole('img', { name: /the served cut 0\.36 has .*; the explored cut 0\.50 has / }),
    ).toBeInTheDocument();
  });

  it('draws the no-skill line at the precision of flagging everyone, as served', async () => {
    await showPerformance();
    expect(card('Precision against recall')).toHaveTextContent('no skill: the churn rate, 0.260');
  });

  it('loads a clicked cut into the errors card, adding one history entry', async () => {
    await showPerformance();
    const added = entriesAdded();
    pick(50);
    expect(errorsTable()).toHaveAccessibleName('Errors at 0.50');
    expect(added()).toBe(1);
  });

  it('loads the first cut too', async () => {
    await showPerformance();
    pick(0);
    expect(errorsTable()).toHaveAccessibleName('Errors at 0.00');
  });

  it('keeps the pointer layer out of the keyboard’s way', async () => {
    await showPerformance();
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
    await showPerformance();
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
    const segments = card('Does one cut serve every segment?');
    expect(segments).toHaveTextContent(UNSCANNED);
    expect(segments).toHaveTextContent(GENDER);
  });

  it('offers no test breakdown, whatever is recorded', async () => {
    await showPerformance('/performance', RECORDED);
    const segments = card('Does one cut serve every segment?');
    expect(within(segments).queryByRole('group', { name: /Partition/ })).toBeNull();
    expect(within(segments).queryByRole('button', { name: 'Test · recorded' })).toBeNull();
    expect(segmentTable(/^Segments at 0\.36, validation$/)).toBeInTheDocument();
  });
});

describe('calibration', () => {
  it('tables the served reliability bins, and leaves a small bin without a rate', async () => {
    await showPerformance();
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

  it('says on the chart which bins are too small to draw, and the served ECE', async () => {
    await showPerformance();
    const calibration = card('Does a probability mean what it says?');
    expect(calibration).toHaveTextContent(
      'Not drawn: 0.8–0.9 (14 customers), fewer than 20 customers.',
    );
    expect(calibration).toHaveTextContent(/ECE 0\.031\. /);
  });

  it('draws no bin, and says so, when none is served', async () => {
    await showPerformance(
      '/performance',
      performance({ validation_evaluation: measured({ reliability: [] }) }),
    );
    expect(
      screen.getByRole('img', {
        name: 'Observed churn against mean stated probability in 0 validation bins.',
      }),
    ).toBeInTheDocument();
  });

  it('compares the uncalibrated and served model at 0.50 from the record', async () => {
    await showPerformance();
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
      '/performance',
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
});

describe('model selection', () => {
  it('charts the candidates inside one standard error, and tables all of them', async () => {
    await showPerformance();
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

  it('states the lead the server gives, and works none out', async () => {
    const figures = performance();
    await showPerformance(
      '/performance',
      performance({ selection: figures.selection && { ...figures.selection, leader_gap: 0.1234 } }),
    );
    expect(card('Model selection: the one-standard-error rule')).toHaveTextContent(
      'Random forest leads Logistic regression by 0.1234,',
    );
  });

  it('links to the model card, and to no report holding figures the policy does not publish', async () => {
    await showPerformance('/performance', RECORDED);
    expect(screen.getByRole('link', { name: /Model card/ })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Final evaluation/ })).toBeNull();
  });

  it('marks the leader the server names, not the highest score', async () => {
    const figures = performance();
    await showPerformance(
      '/performance',
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
      '/performance',
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
      '/performance',
      performance({ selection: figures.selection && { ...figures.selection, cutoff: 0.6501 } }),
    );
    expect(screen.getByText('cutoff 0.6501')).toBeInTheDocument();
  });

  it.each([
    ['the comparison', { candidates: null }],
    ['the selection', { selection: null }],
  ])('says the comparison is not recorded when %s is missing', async (_, missing) => {
    await showPerformance('/performance', performance(missing));
    expect(card('Model selection: the one-standard-error rule')).toHaveTextContent(
      'The model comparison is not recorded in this artifact’s reports.',
    );
  });
});

describe('the words on the page', () => {
  it('claim no cause or verdict, with or without a recorded test evaluation', async () => {
    const first = await showPerformance();
    expectNoClaims();
    expect(document.body).toHaveTextContent('This page computes nothing from the test partition');
    first.unmount();
    await showPerformance('/performance', RECORDED);
    expectNoClaims();
  });
});
