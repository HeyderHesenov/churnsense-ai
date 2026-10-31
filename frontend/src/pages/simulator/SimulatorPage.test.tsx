import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw/http';
import type { Schemas } from '../../api/client';
import type { Sweep } from '../../api/queries';
import { App } from '../../app/App';
import { modelInfo } from '../../test/fixtures';
import { entriesAdded } from '../../test/history';
import { press } from '../../test/keyboard';
import { renderAt } from '../../test/render';
import { answer, server } from '../../test/server';
import { DEFAULTS, echoOf, gridAt, sweepApi, sweepFor } from '../../test/simulatorApi';
import { RESULTS_ID } from './Results';

const PATH = '/api/threshold/sweep';

/** The query of every sweep asked for from now on, in order. */
function sweepsAsked(): URLSearchParams[] {
  const asked: URLSearchParams[] = [];
  server.events.on('request:start', ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname === PATH) asked.push(url.searchParams);
  });
  return asked;
}

/** A query as a plain object, to compare. */
function query(params: URLSearchParams | undefined): Record<string, string> {
  return Object.fromEntries(params ?? []);
}

const ASKED_BY_DEFAULT = { cost: '50', success: '0.3', horizon: '12', margin: '0.65' };

/** The page at ``path``; ``before`` handlers answer ahead of the fixture sweep. */
async function showSimulator(path = '/simulator', before: Parameters<typeof server.use> = []) {
  server.use(sweepApi());
  if (before.length) server.use(...before);
  const rendered = renderAt(<App />, path);
  await screen.findByRole('heading', { level: 1, name: 'Where should the cut-off sit?' });
  return rendered;
}

/** Wait for the figures of a response, from its echo line. */
async function figuresFor(text: RegExp | string = /^Figures for a/) {
  return screen.findByText(text, { selector: 'p' });
}

/** The value a figure's term names, in the results. */
function figure(term: string): string {
  const results = screen.getByRole('region', { name: /^At / });
  const dt = within(results).getByText(term, { selector: 'dt' });
  return dt.nextElementSibling?.textContent ?? '';
}

function pinned() {
  return screen.getByRole('table', { name: 'Pinned scenarios' });
}

/** The pinned rows, without the row for the figures on screen. */
function pinRows() {
  return within(pinned())
    .getAllByRole('row')
    .filter((row) => within(row).queryByRole('button', { name: /^Remove/ }));
}

/** Released after every test, so a failed one never leaves a request waiting. */
const gates: (() => void)[] = [];
afterEach(() => {
  for (const open of gates.splice(0)) open();
});

/** Sweeps after the first ``free`` wait until ``release``. */
function holdAfter(free: number) {
  let release: () => void = () => undefined;
  const opened = new Promise<void>((resolve) => (release = resolve));
  gates.push(() => release());
  let seen = 0;
  const handler = http.get(PATH, async ({ request }) => {
    seen += 1;
    if (seen > free) await opened;
    return HttpResponse.json(sweepFor(echoOf(request)));
  });
  return { handler, release: () => release() };
}

function refusal(status: number, error: string, problems: string[] = [], headers = {}) {
  const body: Schemas['ErrorResponse'] = { error, detail: 'Refused by a test.', problems };
  return http.get(PATH, () => HttpResponse.json(body, { status, headers }));
}

/**
 * Point 40 with every count and amount its own, and none derivable from the
 * others (241 of 512 is not 0.123, 70,321.5 less 25,600 is not 31,234.5): a
 * page that worked a figure out instead of reading it would show another.
 * ``t=0.40`` shows it.
 */
const AT_40 = {
  flagged: 512,
  true_positives: 241,
  false_positives: 9,
  false_negatives: 7,
  precision: 0.123,
  recall: 0.456,
  intervention_cost: 25_600,
  retained_value: 70_321.5,
  net_benefit: 31_234.5,
};

function with40(point: Partial<Sweep['points'][number]>, changes: Partial<Sweep> = {}) {
  return sweepApi((echo) => ({ ...sweepFor(echo, { 40: point }), ...changes }));
}

/** ``text`` put in a box at once, as a paste is: no prefix of it settles on its own. */
function typeInto(label: string, text: string) {
  const box = screen.getByLabelText(label);
  fireEvent.change(box, { target: { value: text } });
  return Promise.resolve(box);
}

/** Every word a page in this state shows: none claims a retention, a saving or a cause. */
function expectNoClaims() {
  const text = document.body.textContent;
  expect(text).not.toMatch(/\b(saved?|saves|rescued?|prevented|guarantee[sd]?)\b/i);
  expect(text).not.toMatch(
    /\b(causes|caused by|causing|drives|driven by|driving|will prevent|prevents)\b/i,
  );
}

describe('the assumptions', () => {
  it('start from the served defaults within the served bounds, and ask once with all four', async () => {
    const asked = sweepsAsked();
    await showSimulator();
    await figuresFor();
    expect(screen.getByLabelText('Cost of one offer')).toHaveValue('50');
    expect(screen.getByLabelText('Revenue horizon')).toHaveValue('12');
    const success = screen.getByRole('slider', { name: 'Offer success rate' });
    expect(success).toHaveAttribute('aria-valuetext', '30%');
    expect(success).toHaveAttribute('min', '0');
    expect(success).toHaveAttribute('max', '80');
    const margin = screen.getByRole('slider', { name: 'Gross margin' });
    expect(margin).toHaveAttribute('min', '10');
    expect(margin).toHaveAttribute('max', '90');
    expect(screen.getByText('$1 to $500 · default $50')).toBeInTheDocument();
    expect(screen.getByText('0% to 80% · default 30%')).toBeInTheDocument();
    expect(screen.getByText('2 to 60 months · default 12 months')).toBeInTheDocument();
    expect(screen.getByText('10% to 90% · default 65%')).toBeInTheDocument();
    expect(asked.map(query)).toEqual([ASKED_BY_DEFAULT]);
  });

  it('send a settled edit once, after typing in quick succession has stopped', async () => {
    const asked = sweepsAsked();
    await showSimulator();
    await figuresFor();
    const box = screen.getByLabelText('Cost of one offer');
    // No pause between keystrokes: they run back to back, so the settle's timer
    // cannot fire between two of them however busy the machine is. "1" and
    // "12" are valid and would be asked for alone.
    const user = userEvent.setup({ delay: null });
    await user.clear(box);
    await user.type(box, '12.5');
    await figuresFor(/^Figures for a \$12\.50 offer/);
    expect(asked.map(query)).toEqual([ASKED_BY_DEFAULT, { ...ASKED_BY_DEFAULT, cost: '12.5' }]);
    expect(window.location.search).toBe('?cost=12.5');
  });

  it('accept each served bound', async () => {
    const asked = sweepsAsked();
    await showSimulator();
    await figuresFor();
    await typeInto('Cost of one offer', '1');
    await figuresFor(/^Figures for a \$1 offer/);
    await typeInto('Cost of one offer', '500');
    await figuresFor(/^Figures for a \$500 offer/);
    await typeInto('Revenue horizon', '2');
    await figuresFor(/ a 2-month horizon/);
    await typeInto('Revenue horizon', '60');
    await figuresFor(/ a 60-month horizon/);
    expect(asked.map((q) => [q.get('cost'), q.get('horizon')])).toEqual([
      ['50', '12'],
      ['1', '12'],
      ['500', '12'],
      ['500', '2'],
      ['500', '60'],
    ]);
  });

  it.each([
    ['Cost of one offer', '500.01', 'The cost of one offer is $1 to $500.'],
    ['Cost of one offer', '0.5', 'The cost of one offer is $1 to $500.'],
    ['Cost of one offer', '12.345', 'The cost of one offer has at most 2 decimal places.'],
    [
      'Cost of one offer',
      '-',
      'The cost of one offer is a number, written in digits with a point for any decimals.',
    ],
    [
      'Cost of one offer',
      '12,50',
      'The cost of one offer is a number, written in digits with a point for any decimals.',
    ],
    [
      'Cost of one offer',
      '1e3',
      'The cost of one offer is a number, written in digits with a point for any decimals.',
    ],
    [
      'Cost of one offer',
      '$50',
      'The cost of one offer is a number, written in digits with a point for any decimals.',
    ],
    ['Revenue horizon', '2.5', 'The horizon is a whole number of months.'],
    ['Revenue horizon', '61', 'The horizon is 2 to 60 months.'],
  ])('send nothing for %s %j, say why, and mark the older figures', async (label, text, why) => {
    const asked = sweepsAsked();
    await showSimulator();
    await figuresFor();
    const box = await typeInto(label, text);
    const message = await screen.findByText(why);
    expect(box).toHaveAttribute('aria-invalid', 'true');
    expect(box.getAttribute('aria-describedby')).toContain(message.id);
    expect(
      await screen.findByText(
        'Not sent: correct the marked field. The figures below are for the assumptions they name.',
      ),
    ).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(asked).toHaveLength(1);
  });

  it.each([
    ['?success=0.295', 'The success rate is in whole percents.'],
    ['?cost=', 'Enter the cost of one offer.'],
    ['?margin=0.95', 'The gross margin is 10% to 90%.'],
  ])('ask for nothing from an address the fields refuse (%s)', async (search, why) => {
    const asked = sweepsAsked();
    await showSimulator(`/simulator${search}`);
    expect(await screen.findByText(why)).toBeInTheDocument();
    expect(
      screen.getByText('Not sent: correct the marked field.', { selector: 'p' }),
    ).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(asked).toEqual([]);
  });

  it('are written in the served currency', async () => {
    const info = modelInfo();
    if (!info.business_defaults) throw new Error('the fixture serves its defaults');
    const euros = { ...info.business_defaults, currency: 'EUR' };
    await showSimulator('/simulator?t=0.40', [
      answer('get', '/api/model-info', { ...info, business_defaults: euros }),
      sweepApi((echo) => sweepFor({ ...echo, currency: 'EUR' }, { 40: AT_40 })),
    ]);
    await figuresFor(/^Figures for a €50 offer/);
    expect(screen.getByText('€1 to €500 · default €50')).toBeInTheDocument();
    expect(figure('Net benefit')).toBe('+€31,235 net gain');
    expect(figure('Retained value')).toBe('€70,322');
    expect(figure('Campaign cost')).toBe('€25,600');
    expect(
      screen.getByText(/^Recommended under these assumptions: 0\.\d\d · [+−]€/),
    ).toBeInTheDocument();
    await press(screen.getByRole('button', { name: 'Pin these figures' }));
    const [row] = pinRows();
    expect(row).toHaveTextContent('€50 · 30% · 12 mo · 65%');
    expect(row).toHaveTextContent('+€31,235');
  });

  it('offer a number pad for whole months and a decimal one for money', async () => {
    await showSimulator();
    await figuresFor();
    expect(screen.getByLabelText('Revenue horizon')).toHaveAttribute('inputmode', 'numeric');
    expect(screen.getByLabelText('Cost of one offer')).toHaveAttribute('inputmode', 'decimal');
  });

  it('are told apart from what is measured and what is simulated', async () => {
    await showSimulator();
    await figuresFor();
    expect(screen.getByRole('heading', { name: 'Your assumptions' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Measured on validation' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /^Simulated money/ })).toBeInTheDocument();
    expect(screen.getByText(/^Simulated, not measured\./)).toBeInTheDocument();
  });
});

describe('the figures at the cut', () => {
  it('each come from their own field of the response', async () => {
    await showSimulator('/simulator?t=0.40', [
      with40(AT_40, {
        recommended: {
          threshold: 0.36,
          flagged: 600,
          true_positives: 250,
          precision: 0.41,
          recall: 0.8,
          intervention_cost: 30_000,
          retained_value: 75_000.4,
          net_benefit: 45_000.4,
        },
      }),
    ]);
    await figuresFor(
      'Figures for a $50 offer, 30% success, a 12-month horizon and 65% margin, ' +
        'on 1,250 validation customers (310 churned).',
    );
    expect(screen.getByRole('heading', { name: 'At 0.40' })).toBeInTheDocument();
    expect(figure('Flagged')).toBe('512 of 1,250');
    expect(figure('Churners reached')).toBe('241 of 310');
    expect(figure('Churners missed')).toBe('7');
    expect(figure('Offers to customers who stayed')).toBe('9');
    expect(figure('Precision')).toBe('0.123');
    expect(figure('Recall')).toBe('0.456');
    expect(figure('Net benefit')).toBe('+$31,235 net gain');
    expect(figure('Retained value')).toBe('$70,322');
    expect(figure('Campaign cost')).toBe('$25,600');
    expect(
      screen.getByText('Recommended under these assumptions: 0.36 · +$45,000'),
    ).toBeInTheDocument();
  });

  it('say a loss, break-even and millions in words as well as figures', async () => {
    await showSimulator('/simulator?t=0.40', [
      with40({ ...AT_40, net_benefit: -1_200, retained_value: 14_090_000 }),
    ]);
    await figuresFor();
    expect(figure('Net benefit')).toBe('−$1,200 net loss');
    expect(figure('Retained value')).toBe('$14,090,000');
  });

  it('say break-even for a net that rounds to nothing', async () => {
    await showSimulator('/simulator?t=0.40', [with40({ ...AT_40, net_benefit: -0.3 })]);
    await figuresFor();
    expect(figure('Net benefit')).toBe('$0 break-even');
  });

  it('show no precision where nothing is flagged, and no recall without churners', async () => {
    await showSimulator('/simulator?t=0.40', [
      sweepApi((echo) => ({
        ...sweepFor(echo, { 40: { ...AT_40, flagged: 0, true_positives: 0, precision: 0 } }),
        churners: 0,
      })),
    ]);
    await figuresFor();
    expect(figure('Precision')).toBe('—');
    expect(figure('Recall')).toBe('—');
  });

  it('follow the address to a cut, an inexact grid value included', async () => {
    await showSimulator('/simulator?t=0.35');
    await figuresFor();
    expect(screen.getByRole('heading', { name: 'At 0.35' })).toBeInTheDocument();
    expect(screen.getByRole('slider', { name: 'Decision threshold' })).toHaveAttribute(
      'aria-valuetext',
      '0.35',
    );
    expect(screen.getByText('Set by hand')).toBeInTheDocument();
  });
});

describe('figures for other assumptions', () => {
  it('are dimmed and named while the next arrive, and cannot be pinned', async () => {
    const held = holdAfter(1);
    await showSimulator('/simulator', [held.handler]);
    await figuresFor();
    await typeInto('Cost of one offer', '60');
    expect(
      await screen.findByText(
        'Updating. Until the new figures arrive, those below are for the assumptions they name.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('region', { name: /^At / })).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByText(/^Figures for a \$50 offer/, { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pin these figures' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    act(() => held.release());
    await figuresFor(/^Figures for a \$60 offer/);
    expect(screen.getByRole('button', { name: 'Pin these figures' })).not.toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });
});

describe('figures for other assumptions, in every card', () => {
  const STALE = 'Not the assumptions set.';

  it('are named in each reading, and marked once the assumptions set differ', async () => {
    await showSimulator();
    await figuresFor();
    const cards = [
      'Where to cut',
      'Net benefit across every cut',
      'Why not simply take the peak?',
      'If the success rate were…',
    ];
    const charts = [/^Simulated net benefit at /, /^Precision and recall at each cut/];
    const rates = () => screen.getByRole('region', { name: 'If the success rate were…' });
    for (const name of cards) {
      const card = screen.getByRole('region', { name });
      expect(within(card).getByText('For $50 · 30% · 12 mo · 65%')).toBeInTheDocument();
      expect(within(card).queryByText(STALE)).not.toBeInTheDocument();
    }
    for (const name of charts) expect(screen.getByRole('img', { name })).toBeInTheDocument();
    expect(within(rates()).getByText('YOURS')).toBeInTheDocument();
    await typeInto('Cost of one offer', '-');
    await screen.findByText(/^Not sent: correct the marked field\./, { selector: 'p' });
    for (const name of cards) {
      const card = screen.getByRole('region', { name });
      expect(within(card).getByText('For $50 · 30% · 12 mo · 65%')).toBeInTheDocument();
      expect(within(card).getByText(STALE)).toBeInTheDocument();
    }
    const staleCharts = [
      /^Not the assumptions set\. Simulated net benefit at /,
      /^Not the assumptions set\. Precision and recall at each cut/,
    ];
    for (const name of staleCharts) expect(screen.getByRole('img', { name })).toBeInTheDocument();
    // The rate those figures were for is not the reader's while another can be set.
    expect(within(rates()).queryByText('YOURS')).not.toBeInTheDocument();
  });

  it('keep a cut chosen on them, rather than follow their recommendation', async () => {
    await showSimulator();
    await figuresFor();
    await typeInto('Cost of one offer', '-');
    await screen.findByText(/^Not sent: correct the marked field\./, { selector: 'p' });
    // 0.54 is the recommendation of the figures shown, not of assumptions that can be sent.
    const slider = screen.getByRole('slider', { name: 'Decision threshold' });
    fireEvent.change(slider, { target: { value: '50' } });
    fireEvent.change(slider, { target: { value: '54' } });
    expect(new URLSearchParams(window.location.search).get('t')).toBe('0.54');
    // Current again, 0.54 is the recommendation, but set by hand: it is not followed.
    await typeInto('Cost of one offer', '50');
    // The figures shown were for $50 all along: wait for them to be current, not just named.
    await waitFor(() => expect(screen.queryByText(STALE)).not.toBeInTheDocument());
    expect(screen.getByText('Set by hand')).toBeInTheDocument();
    const useRecommended = screen.getByRole('button', { name: 'Use recommended' });
    expect(useRecommended).not.toHaveAttribute('aria-disabled', 'true');
    await press(useRecommended);
    expect(new URLSearchParams(window.location.search).get('t')).toBeNull();
    expect(screen.getByText('Following the recommendation')).toBeInTheDocument();
  });

  it('are said to be for other assumptions when the server echoes others', async () => {
    await showSimulator('/simulator', [sweepApi(() => sweepFor(DEFAULTS))]);
    await figuresFor();
    await typeInto('Cost of one offer', '60');
    expect(
      await screen.findByText(
        'The server answered for other assumptions than those set: the figures below are for the ones they name.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pin these figures' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    const rate = screen.getByRole('button', { name: 'Apply 20% success' });
    expect(rate).toHaveAttribute('aria-disabled', 'true');
    expect(rate).toHaveAttribute('aria-describedby', 'rates-stale');
  });

  it('cannot be applied as a rate, and leave the pinned comparison without a now row', async () => {
    await showSimulator();
    await figuresFor();
    await press(screen.getByRole('button', { name: 'Pin these figures' }));
    expect(screen.getByText('NOW')).toBeInTheDocument();
    await typeInto('Cost of one offer', '500.01');
    await screen.findByText(/^Not sent: correct the marked field\. The figures/, { selector: 'p' });
    expect(screen.queryByText('NOW')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply 20% success' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });
});

describe('when the sweep cannot be had', () => {
  it('says so once outcomes are withheld, and asks no more', async () => {
    const asked = sweepsAsked();
    await showSimulator('/simulator', [refusal(503, 'outcomes_withheld')]);
    expect(await screen.findByText(/validation outcomes are withheld/)).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 700));
    await typeInto('Cost of one offer', '60');
    // Once the edit is in the address, a page that asked would have asked by the next tick or two.
    await waitFor(() => expect(window.location.search).toBe('?cost=60'));
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(asked).toHaveLength(1);
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Compare scenarios' })).not.toBeInTheDocument();
    expectNoClaims();
  });

  it('says so, with nothing to try again, when the served model gives no assumptions', async () => {
    const asked = sweepsAsked();
    await showSimulator('/simulator', [
      answer('get', '/api/model-info', modelInfo({ assumption_bounds: null })),
    ]);
    expect(
      await screen.findByText(/does not say which assumptions it accepts/),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
    expect(asked).toEqual([]);
  });

  it('moves the focus to the results when a rate applied cannot be had', async () => {
    let failing = false;
    const flaky = http.get(PATH, ({ request }) => {
      const echo = echoOf(request);
      if (echo.offer_success_rate === 0.2) failing = true;
      if (failing)
        return HttpResponse.json(
          { error: 'internal_error', detail: 'x', problems: [] },
          { status: 500 },
        );
      return HttpResponse.json(sweepFor(echo));
    });
    await showSimulator('/simulator', [flaky]);
    await figuresFor();
    await press(screen.getByRole('button', { name: 'Apply 20% success' }));
    await screen.findByRole('button', { name: 'Try again' }, { timeout: 3000 });
    expect(document.activeElement).toBe(screen.getByRole('heading', { name: /^At / }));
  });

  it('moves the focus to the results when a rate applied finds the outcomes withheld', async () => {
    let withholding = false;
    const turning = http.get(PATH, ({ request }) => {
      const echo = echoOf(request);
      if (echo.offer_success_rate === 0.2) withholding = true;
      if (withholding)
        return HttpResponse.json(
          { error: 'outcomes_withheld', detail: 'x', problems: [] },
          { status: 503 },
        );
      return HttpResponse.json(sweepFor(echo));
    });
    await showSimulator('/simulator', [turning]);
    await figuresFor();
    await press(screen.getByRole('button', { name: 'Apply 20% success' }));
    expect(await screen.findByText(/validation outcomes are withheld/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Apply 20% success' })).not.toBeInTheDocument();
    await waitFor(() => expect(document.activeElement).toBe(document.getElementById(RESULTS_ID)));
  });

  it('asks for nothing when the served model says its partitions are unverified', async () => {
    const asked = sweepsAsked();
    const info = modelInfo({
      provenance: {
        verified: false,
        data_sha256: null,
        split_digest: null,
        withheld_reason: 'unverified',
      },
    });
    await showSimulator('/simulator', [answer('get', '/api/model-info', info)]);
    expect(await screen.findByText(/validation outcomes are withheld/)).toBeInTheDocument();
    await typeInto('Cost of one offer', '60');
    await waitFor(() => expect(window.location.search).toBe('?cost=60'));
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(asked).toEqual([]);
  });

  it('asks again once on its own after a server failure, then on request, keeping the focus', async () => {
    const asked = sweepsAsked();
    let seen = 0;
    const flaky = http.get(PATH, ({ request }) => {
      seen += 1;
      if (seen <= 2)
        return HttpResponse.json(
          { error: 'internal_error', detail: 'x', problems: [] },
          { status: 500 },
        );
      return HttpResponse.json(sweepFor(echoOf(request)));
    });
    await showSimulator('/simulator', [flaky]);
    const retry = await screen.findByRole('button', { name: 'Try again' }, { timeout: 3000 });
    expect(asked).toHaveLength(2);
    await press(retry);
    expect(document.activeElement).toBe(screen.getByRole('heading', { name: /^At / }));
    await figuresFor();
    expect(asked).toHaveLength(3);
  });

  it('does not ask again on its own after a refusal to answer so often', async () => {
    const asked = sweepsAsked();
    // Even "ask again at once" is not taken as leave to retry on its own.
    await showSimulator('/simulator', [refusal(429, 'rate_limited', [], { 'Retry-After': '0' })]);
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(asked).toHaveLength(1);
  });

  it('says a request was refused even when the API lists no problem', async () => {
    await showSimulator('/simulator', [refusal(422, 'validation_error')]);
    expect(await screen.findByText('The API refused these assumptions.')).toBeInTheDocument();
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
  });

  it('lists the problems of a refused request, with nothing to try again', async () => {
    await showSimulator('/simulator', [
      refusal(422, 'validation_error', ['cost: Input should be less than or equal to 10000']),
    ]);
    expect(
      await screen.findByText('cost: Input should be less than or equal to 10000'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
  });
});

describe('the cut', () => {
  it('follows the recommendation until set, and goes back to it on request', async () => {
    await showSimulator();
    await figuresFor();
    expect(screen.getByText('Following the recommendation')).toBeInTheDocument();
    const useRecommended = screen.getByRole('button', { name: 'Use recommended' });
    expect(useRecommended).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(screen.getByRole('slider', { name: 'Decision threshold' }), {
      target: { value: '40' },
    });
    expect(window.location.search).toBe('?t=0.40');
    expect(screen.getByText('Set by hand')).toBeInTheDocument();
    const added = entriesAdded();
    await press(useRecommended);
    expect(window.location.search).toBe('');
    expect(added()).toBe(1);
    expect(document.activeElement).toBe(useRecommended);
    expect(screen.getByText('Following the recommendation')).toBeInTheDocument();
  });

  it('is set in place by the slider, and as a new entry by a click on the chart', async () => {
    await showSimulator();
    await figuresFor();
    const added = entriesAdded();
    fireEvent.change(screen.getByRole('slider', { name: 'Decision threshold' }), {
      target: { value: '40' },
    });
    expect(added()).toBe(0);
    const chart = screen.getByRole('region', { name: 'Net benefit across every cut' });
    const layer = chart.querySelector('[aria-hidden="true"][class*="capture"]');
    if (!layer) throw new Error('the chart’s pointer layer');
    // jsdom lays nothing out: the plot is given a width, and the pointer 70% across it.
    layer.getBoundingClientRect = () => new DOMRect(0, 0, 1000, 100);
    fireEvent.click(layer, { clientX: 700 });
    expect(window.location.search).toBe('?t=0.70');
    expect(added()).toBe(1);
  });

  it('resets to the defaults from the keyboard, and says when it is there', async () => {
    await showSimulator('/simulator?cost=80&t=0.40');
    await figuresFor(/^Figures for a \$80 offer/);
    const reset = screen.getByRole('button', { name: 'Reset to defaults' });
    const added = entriesAdded();
    await press(reset);
    expect(window.location.search).toBe('');
    expect(added()).toBe(1);
    await figuresFor(/^Figures for a \$50 offer/);
    expect(reset).toHaveAttribute('aria-disabled', 'true');
    expect(document.activeElement).toBe(reset);
  });

  it('resets an edit made a moment before, not yet in the address', async () => {
    await showSimulator();
    await figuresFor();
    await typeInto('Cost of one offer', '60');
    // Within the settle: the address is still at the defaults.
    expect(window.location.search).toBe('');
    await press(screen.getByRole('button', { name: 'Reset to defaults' }));
    expect(window.location.search).toBe('');
    expect(screen.getByLabelText('Cost of one offer')).toHaveValue('50');
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(window.location.search).toBe('');
    expect(screen.getByLabelText('Cost of one offer')).toHaveValue('50');
  });
});

describe('if the success rate were…', () => {
  it('applies a rate, follows its recommendation, and keeps the focus', async () => {
    await showSimulator('/simulator?t=0.40');
    await figuresFor();
    const row = screen.getByRole('button', { name: 'Apply 20% success' });
    await press(row);
    expect(window.location.search).toBe('?success=0.2');
    await figuresFor(/20% success/);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Apply 20% success' }));
  });

  it('offers no rate outside the served bounds', async () => {
    const info = modelInfo();
    const bounds = info.assumption_bounds;
    if (!bounds) throw new Error('the fixture serves its bounds');
    await showSimulator('/simulator', [
      answer('get', '/api/model-info', {
        ...info,
        assumption_bounds: { ...bounds, offer_success_rate: { min: 0, max: 0.4, integer: false } },
      }),
    ]);
    await figuresFor();
    expect(screen.getByRole('button', { name: 'Apply 50% success' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Apply 40% success' })).not.toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('leaves Back to the edit before it', async () => {
    await showSimulator();
    await figuresFor();
    await typeInto('Cost of one offer', '60');
    await figuresFor(/^Figures for a \$60 offer/);
    await press(screen.getByRole('button', { name: 'Apply 20% success' }));
    await figuresFor(/20% success/);
    act(() => window.history.back());
    await waitFor(() => expect(window.location.search).toBe('?cost=60'));
    expect(screen.getByLabelText('Cost of one offer')).toHaveValue('60');
    expect(screen.getByRole('slider', { name: 'Offer success rate' })).toHaveAttribute(
      'aria-valuetext',
      '30%',
    );
  });
});

describe('pinned scenarios', () => {
  it('keep a response as it came, and set it again on Apply', async () => {
    // Below the fixture's peak, so 0.40 is a cut set by hand, not the recommendation.
    await showSimulator('/simulator?t=0.40', [with40({ ...AT_40, net_benefit: 24_721.5 })]);
    await figuresFor();
    await press(screen.getByRole('button', { name: 'Pin these figures' }));
    const [row] = pinRows();
    if (!row) throw new Error('a pinned row');
    expect(row).toHaveTextContent('$50 · 30% · 12 mo · 65%');
    expect(row).toHaveTextContent('0.40');
    expect(row).toHaveTextContent('512');
    expect(row).toHaveTextContent('0.456');
    expect(row).toHaveTextContent('+$24,722');
    await typeInto('Cost of one offer', '60');
    await figuresFor(/^Figures for a \$60 offer/);
    const now = within(pinned()).getAllByRole('row')[1];
    expect(now).toHaveTextContent(/^NOW \$60 · 30% · 12 mo · 65%0\.40/);
    const added = entriesAdded();
    await press(within(row).getByRole('button', { name: /^Apply/ }));
    expect(window.location.search).toBe('?cost=50&success=0.3&horizon=12&margin=0.65&t=0.40');
    expect(added()).toBe(1);
    await figuresFor(/^Figures for a \$50 offer/);
    expect(figure('Net benefit')).toBe('+$24,722 net gain');
  });

  it('refuse the same scenario twice, and a fifth', async () => {
    await showSimulator();
    await figuresFor();
    const pin = screen.getByRole('button', { name: 'Pin these figures' });
    await press(pin);
    expect(pin).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('These figures are pinned.')).toBeInTheDocument();
    const slider = screen.getByRole('slider', { name: 'Decision threshold' });
    for (const at of ['41', '42', '43']) {
      fireEvent.change(slider, { target: { value: at } });
      await press(pin);
    }
    expect(pinRows()).toHaveLength(4);
    fireEvent.change(slider, { target: { value: '44' } });
    expect(pin).toHaveAttribute('aria-disabled', 'true');
    expect(
      screen.getByText('Four scenarios are pinned: remove one to pin another.'),
    ).toBeInTheDocument();
    await press(pin);
    expect(pinRows()).toHaveLength(4);
  });

  it('move the focus to the next Remove, then to Pin when none is left', async () => {
    await showSimulator();
    await figuresFor();
    const pin = screen.getByRole('button', { name: 'Pin these figures' });
    await press(pin);
    fireEvent.change(screen.getByRole('slider', { name: 'Decision threshold' }), {
      target: { value: '41' },
    });
    await press(pin);
    const [first, second] = within(pinned()).getAllByRole('button', { name: /^Remove/ });
    if (!first || !second) throw new Error('two pins');
    await press(first);
    expect(document.activeElement).toBe(second);
    await press(second);
    expect(document.activeElement).toBe(pin);
    expect(screen.getByText(/^Nothing pinned yet/)).toBeInTheDocument();
  });

  it('move the focus to the Remove before, when the last is removed', async () => {
    await showSimulator();
    await figuresFor();
    const pin = screen.getByRole('button', { name: 'Pin these figures' });
    await press(pin);
    fireEvent.change(screen.getByRole('slider', { name: 'Decision threshold' }), {
      target: { value: '41' },
    });
    await press(pin);
    const [first, second] = within(pinned()).getAllByRole('button', { name: /^Remove/ });
    if (!first || !second) throw new Error('two pins');
    await press(second);
    expect(document.activeElement).toBe(first);
  });
});

describe('the words on the page', () => {
  it('never claim a retention, a saving or a cause', async () => {
    await showSimulator();
    await figuresFor();
    const text = document.body.textContent;
    expect(text).not.toMatch(/\b(saved?|saves|rescued?|prevented|guarantee[sd]?)\b/i);
    expect(text).not.toMatch(/\b(causes|caused by|drives|driven by|will prevent)\b/i);
  });
});

describe('nothing worked out in the page', () => {
  it('takes the recommended cut from the response, wherever it is', async () => {
    // Point 90 is nowhere near the fixture's peak: only a page that reads
    // ``recommended`` lands there.
    await showSimulator('/simulator', [
      sweepApi((echo) => {
        const sweep = sweepFor(echo);
        return { ...sweep, recommended: { ...sweep.recommended, threshold: gridAt(90) } };
      }),
    ]);
    await figuresFor();
    expect(screen.getByRole('heading', { name: 'At 0.90' })).toBeInTheDocument();
    expect(screen.getByText('Following the recommendation')).toBeInTheDocument();
  });

  it('says so, and draws no cut, when the response has no point at its own recommendation', async () => {
    await showSimulator('/simulator', [
      sweepApi((echo) => {
        const sweep = sweepFor(echo);
        // No point of a 0-to-1 grid is labelled 1.50.
        return { ...sweep, recommended: { ...sweep.recommended, threshold: 1.5 } };
      }),
    ]);
    expect(await screen.findByText(/has no point at its own recommended cut/)).toBeInTheDocument();
    expect(screen.queryByRole('slider', { name: 'Decision threshold' })).not.toBeInTheDocument();
    expect(screen.queryByText('Loading the sweep')).not.toBeInTheDocument();
  });

  it('keeps the counts, the money and the assumptions apart', async () => {
    await showSimulator('/simulator?t=0.40');
    await figuresFor();
    const terms = (heading: string) => {
      const group = screen.getByRole('heading', { name: heading }).parentElement;
      if (!group) throw new Error(heading);
      return within(group)
        .getAllByRole('term')
        .map((term) => term.textContent);
    };
    expect(terms('Measured on validation')).toEqual([
      'Flagged',
      'Churners reached',
      'Churners missed',
      'Offers to customers who stayed',
      'Precision',
      'Recall',
    ]);
    expect(terms('Simulated money')).toEqual(['Net benefit', 'Retained value', 'Campaign cost']);
  });

  it('says the figures that arrive in a live region, with what they are for', async () => {
    await showSimulator();
    await figuresFor();
    const results = screen.getByRole('region', { name: /^At / });
    expect(within(results).getByRole('status')).toHaveTextContent(/Figures for a \$50 offer/);
  });

  it('marks the peak, the recommendation and the cut in the chart’s table', async () => {
    // The recommendation moved off the peak, so each mark is on its own row.
    const sweep = sweepFor(DEFAULTS);
    const moved = { ...sweep, recommended: { ...sweep.recommended, threshold: gridAt(90) } };
    expect(sweep.peak.threshold).not.toBe(gridAt(90));
    await showSimulator('/simulator?t=0.40', [sweepApi(() => moved)]);
    await figuresFor();
    const table = screen.getByRole('table', { name: 'Simulated net benefit by cut' });
    const marks = new Map<string, string>();
    for (const row of within(table).getAllByRole('row').slice(1)) {
      const cells = within(row)
        .getAllByRole('cell')
        .map((cell) => cell.textContent);
      marks.set(cells[0] ?? '', cells[3] ?? '');
    }
    expect(marks.get(sweep.peak.threshold.toFixed(2))).toBe('peak');
    expect(marks.get('0.90')).toBe('recommended');
    expect(marks.get('0.40')).toBe('selected');
  });

  it('states the band from the floor the response gives', async () => {
    // Not the peak less one offer: only a page that reads ``band.floor`` shows it.
    await showSimulator('/simulator', [
      sweepApi((echo) => {
        const sweep = sweepFor(echo);
        return { ...sweep, band: { ...sweep.band, floor: 1_234 } };
      }),
    ]);
    await figuresFor();
    expect(
      screen.getByText(/^nets within one offer of the peak \(\+\$1,234 to /),
    ).toBeInTheDocument();
  });

  it('lists each sensitivity row as the response gives it', async () => {
    const row = (rate: number, threshold: number, net: number) => ({
      offer_success_rate: rate,
      threshold,
      flagged: 3,
      precision: 0.5,
      recall: 0.1,
      net_benefit: net,
    });
    await showSimulator('/simulator', [
      sweepApi((echo) => ({
        ...sweepFor(echo),
        sensitivity: [row(0.1, 0.88, -77), row(0.2, 0.61, 1_111)],
      })),
    ]);
    await figuresFor();
    const item = (name: string) => screen.getByRole('button', { name }).closest('li');
    expect(item('Apply 20% success')).toHaveTextContent('20%cut 0.61+$1,111');
    expect(item('Apply 10% success')).toHaveTextContent('10%cut 0.88−$77');
  });
});

describe('loading', () => {
  it('marks the first sweep busy behind a skeleton', async () => {
    const held = holdAfter(0);
    await showSimulator('/simulator', [held.handler]);
    expect(await screen.findByText('Loading the sweep')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: /^At / })).toHaveAttribute('aria-busy', 'true');
    act(() => held.release());
    await figuresFor();
    expect(screen.getByRole('region', { name: /^At / })).toHaveAttribute('aria-busy', 'false');
  });

  it('shows a skeleton until the served assumptions are in', async () => {
    let release: () => void = () => undefined;
    const opened = new Promise<void>((resolve) => (release = resolve));
    gates.push(() => release());
    await showSimulator('/simulator', [
      http.get('/api/model-info', async () => {
        await opened;
        return HttpResponse.json(modelInfo());
      }),
    ]);
    expect(screen.getByText('Loading the served assumptions')).toBeInTheDocument();
    act(() => release());
    await figuresFor();
  });

  it('says when the served assumptions cannot be had, asks no sweep, and loads them on request', async () => {
    const asked = sweepsAsked();
    let calls = 0;
    await showSimulator('/simulator', [
      http.get('/api/model-info', () => {
        calls += 1;
        // A refusal, so the shell's own query does not retry it first.
        return calls === 1
          ? HttpResponse.json({ error: 'bad_request', detail: 'x', problems: [] }, { status: 400 })
          : HttpResponse.json(modelInfo());
      }),
    ]);
    expect(
      await screen.findByText(/assumptions and their bounds could not be loaded/),
    ).toBeInTheDocument();
    expect(asked).toEqual([]);
    await press(screen.getByRole('button', { name: 'Try again' }));
    await figuresFor();
    expect(calls).toBe(2);
  });
});

describe('the rate sliders', () => {
  it('sit where the address says, and send the rate they show', async () => {
    const asked = sweepsAsked();
    await showSimulator('/simulator?success=0.29');
    await figuresFor(/29% success/);
    const slider = screen.getByRole('slider', { name: 'Offer success rate' });
    expect(slider).toHaveValue('29');
    fireEvent.change(slider, { target: { value: '57' } });
    await figuresFor(/57% success/);
    expect(asked.map((q) => q.get('success'))).toEqual(['0.29', '0.57']);
  });

  it('start and stop at the served bounds, in whole percents', async () => {
    const info = modelInfo();
    const bounds = info.assumption_bounds;
    const defaults = info.business_defaults;
    if (!bounds || !defaults) throw new Error('the fixture serves its bounds and defaults');
    await showSimulator('/simulator', [
      answer('get', '/api/model-info', {
        ...info,
        // 0.07 * 100 and 0.57 * 100 are not whole numbers in floating point.
        assumption_bounds: {
          ...bounds,
          offer_success_rate: { min: 0.07, max: 0.57, integer: false },
          gross_margin: { min: 0.14, max: 0.29, integer: false },
        },
        business_defaults: { ...defaults, gross_margin: 0.2 },
      }),
    ]);
    await figuresFor();
    const ends = (name: string) => {
      const slider = screen.getByRole('slider', { name });
      return [slider.getAttribute('min'), slider.getAttribute('max')];
    };
    expect(ends('Offer success rate')).toEqual(['7', '57']);
    expect(ends('Gross margin')).toEqual(['14', '29']);
  });

  it('mark a rate the address gives that they cannot show', async () => {
    await showSimulator('/simulator?success=0.295');
    const why = await screen.findByText('The success rate is in whole percents.');
    const slider = screen.getByRole('slider', { name: 'Offer success rate' });
    expect(slider).toHaveAttribute('aria-invalid', 'true');
    expect(slider.getAttribute('aria-describedby')).toContain(why.id);
    expect(slider).toHaveAttribute('aria-valuetext', 'not set');
  });

  it('take the lowest rate the server accepts', async () => {
    await showSimulator('/simulator?success=0');
    await figuresFor(/ 0% success/);
    expect(screen.getByRole('slider', { name: 'Offer success rate' })).toHaveAttribute(
      'aria-valuetext',
      '0%',
    );
  });
});

describe('history', () => {
  it('keeps an edit and a cut moved by the slider in place', async () => {
    await showSimulator();
    await figuresFor();
    const added = entriesAdded();
    await typeInto('Cost of one offer', '60');
    await figuresFor(/^Figures for a \$60 offer/);
    fireEvent.change(screen.getByRole('slider', { name: 'Decision threshold' }), {
      target: { value: '41' },
    });
    expect(added()).toBe(0);
  });

  it('writes no cut when the slider is moved onto the recommendation', async () => {
    await showSimulator('/simulator?t=0.40');
    await figuresFor();
    // The fixture's recommendation under the defaults is 0.54.
    fireEvent.change(screen.getByRole('slider', { name: 'Decision threshold' }), {
      target: { value: '54' },
    });
    expect(window.location.search).toBe('');
  });

  it('goes Forward again to the later assumptions and their figures', async () => {
    await showSimulator();
    await figuresFor();
    await press(screen.getByRole('button', { name: 'Apply 20% success' }));
    await figuresFor(/20% success/);
    act(() => window.history.back());
    await figuresFor(/30% success/);
    act(() => window.history.forward());
    await waitFor(() => expect(window.location.search).toBe('?success=0.2'));
    await figuresFor(/20% success/);
  });
});

describe('pins over time', () => {
  it('keep their own figures when the assumptions change, and Apply sets the one chosen', async () => {
    await showSimulator('/simulator?t=0.40', [with40({ ...AT_40, net_benefit: 24_721.5 })]);
    await figuresFor();
    const pin = screen.getByRole('button', { name: 'Pin these figures' });
    await press(pin);
    fireEvent.change(screen.getByRole('slider', { name: 'Decision threshold' }), {
      target: { value: '45' },
    });
    await press(pin);
    await typeInto('Cost of one offer', '60');
    await figuresFor(/^Figures for a \$60 offer/);
    const [first, second] = pinRows();
    if (!first || !second) throw new Error('two pins');
    expect(first).toHaveTextContent(/^\$50 · 30% · 12 mo · 65%0\.40512/);
    expect(first).toHaveTextContent('+$24,722');
    expect(first).toHaveTextContent('0.456');
    expect(second).toHaveTextContent(/^\$50 · 30% · 12 mo · 65%0\.45/);
    await press(within(second).getByRole('button', { name: /^Apply/ }));
    expect(window.location.search).toBe('?cost=50&success=0.3&horizon=12&margin=0.65&t=0.45');
  });

  it('tag a pin at the recommendation, which Apply follows again', async () => {
    await showSimulator();
    await figuresFor();
    await press(screen.getByRole('button', { name: 'Pin these figures' }));
    const [row] = pinRows();
    if (!row) throw new Error('a pin');
    expect(within(row).getByText('REC')).toBeInTheDocument();
    await typeInto('Cost of one offer', '60');
    await figuresFor(/^Figures for a \$60 offer/);
    await press(within(row).getByRole('button', { name: /^Apply/ }));
    expect(new URLSearchParams(window.location.search).get('t')).toBeNull();
  });

  it('tag a cut set by hand at the recommendation, which Apply sets by hand again', async () => {
    await showSimulator();
    await figuresFor();
    // Set on figures for other assumptions, 0.54 is the recommendation once they are current.
    await typeInto('Cost of one offer', '-');
    await screen.findByText(/^Not sent: correct the marked field\./, { selector: 'p' });
    const slider = screen.getByRole('slider', { name: 'Decision threshold' });
    fireEvent.change(slider, { target: { value: '50' } });
    fireEvent.change(slider, { target: { value: '54' } });
    await typeInto('Cost of one offer', '50');
    // The figures shown were for $50 all along: wait for them to be current, not just named.
    const pin = screen.getByRole('button', { name: 'Pin these figures' });
    await waitFor(() => expect(pin).not.toHaveAttribute('aria-disabled', 'true'));
    expect(screen.getByText('Set by hand')).toBeInTheDocument();
    await press(pin);
    const [row] = pinRows();
    if (!row) throw new Error('a pin');
    expect(within(row).getByText('REC')).toBeInTheDocument();
    await typeInto('Cost of one offer', '60');
    await figuresFor(/^Figures for a \$60 offer/);
    await press(within(row).getByRole('button', { name: /^Apply/ }));
    expect(new URLSearchParams(window.location.search).get('t')).toBe('0.54');
  });

  it('are not offered a rate already set, and say so', async () => {
    await showSimulator();
    await figuresFor();
    const set = screen.getByRole('button', { name: 'Apply 30% success' });
    expect(set).toHaveAttribute('aria-disabled', 'true');
    expect(set).toHaveAccessibleDescription('The rate tagged YOURS is the one set.');
  });
});
