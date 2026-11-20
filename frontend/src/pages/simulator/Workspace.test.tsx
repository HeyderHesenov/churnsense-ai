/**
 * The simulator as a scenario workspace (design v4): its three tabs, the cut
 * as a fader over the response's own cuts, the Advanced controls, the
 * overview, the three cuts side by side and the method. What every figure is
 * read from is checked as SimulatorPage.test.tsx checks it: fixtures whose
 * figures cannot be worked out from one another.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw/http';
import type { Sweep } from '../../api/queries';
import { App } from '../../app/App';
import { modelInfo } from '../../test/fixtures';
import { entriesAdded } from '../../test/history';
import { press } from '../../test/keyboard';
import { renderAt } from '../../test/render';
import { server } from '../../test/server';
import { DEFAULTS, echoOf, sweepApi, sweepFor } from '../../test/simulatorApi';

const SWEEP = '/api/threshold/sweep';

/** Released after every test, so a failed one never leaves a request waiting. */
const gates: (() => void)[] = [];
afterEach(() => {
  for (const open of gates.splice(0)) open();
});

/** A gate the test opens, or the end of the test does. */
function gate() {
  let release: () => void = () => undefined;
  const opened = new Promise<void>((resolve) => (release = resolve));
  gates.push(() => release());
  return { opened, release: () => release() };
}
const MODEL_INFO = '/api/model-info';

/** Every request to ``path`` from now on. */
function requestsTo(path: string): URLSearchParams[] {
  const asked: URLSearchParams[] = [];
  server.events.on('request:start', ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname === path) asked.push(url.searchParams);
  });
  return asked;
}

async function showSimulator(path = '/simulator', before: Parameters<typeof server.use> = []) {
  server.use(sweepApi());
  if (before.length) server.use(...before);
  const rendered = renderAt(<App />, path);
  await screen.findByRole('heading', { level: 1, name: 'Where should the cut-off sit?' });
  return rendered;
}

async function figuresFor(text: RegExp | string = /^Figures for a/) {
  return screen.findByText(text, { selector: 'p' });
}

const TABS = { Simulate: /^Simulate/, Compare: /^Compare/, Method: /^Method/ } as const;

async function openTab(name: keyof typeof TABS) {
  await press(screen.getByRole('tab', { name: TABS[name] }));
  await screen.findByRole('tab', { name: TABS[name], selected: true });
}

function address(): Record<string, string> {
  return Object.fromEntries(new URLSearchParams(window.location.search));
}

const cutSlider = () => screen.getByRole('slider', { name: 'Decision threshold' });
const readout = () => screen.getByText(/^At \d\.\d\d: /, { selector: 'p' });

/** A public demo's sweep: five cuts only, the recommendation and the served cut among them. */
function sparse(echo = DEFAULTS): Sweep {
  const fine = sweepFor(echo);
  const kept = [10, 25, 36, 50, 80].flatMap((i) => (fine.points[i] ? [fine.points[i]] : []));
  const at = (i: number) => fine.points[i] ?? fine.points[0];
  const rec = at(50);
  if (!rec) throw new Error('the fixture has point 50');
  return {
    ...fine,
    points: kept,
    recommended: { ...fine.recommended, threshold: rec.threshold },
    peak: { ...fine.peak, threshold: rec.threshold },
  };
}

/**
 * Point 40 with every rate its own: F1 0.789 is not what 0.123 and 0.456
 * would give (0.194), so a page that worked F1 out would show another.
 */
const AT_40 = {
  flagged: 512,
  true_positives: 241,
  false_positives: 9,
  false_negatives: 7,
  precision: 0.123,
  recall: 0.456,
  f1: 0.789,
  intervention_cost: 25_600,
  retained_value: 70_321.5,
  net_benefit: 31_234.5,
};

describe('the tabs', () => {
  it('are Simulate, Compare and Method, Simulate when the address names none', async () => {
    await showSimulator('/simulator?tab=nonsense');
    await figuresFor();
    const tabs = within(screen.getByRole('tablist', { name: 'Simulator sections' }));
    expect(tabs.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      '1Simulate Set assumptions, read the result',
      '2Compare Served, recommended, selected, pinned',
      '3Method Why this cut-off',
    ]);
    expect(tabs.getByRole('tab', { selected: true })).toHaveTextContent(/Simulate/);
    expect(screen.getByRole('region', { name: 'Scenario controls' })).toBeInTheDocument();
  });

  it('are one history entry each, keep the assumptions and the cut, and come back with Back', async () => {
    await showSimulator('/simulator?cost=80&t=0.40');
    await figuresFor(/^Figures for a \$80 offer/);
    const added = entriesAdded();
    await openTab('Compare');
    expect(address()).toEqual({ tab: 'compare', cost: '80', t: '0.40' });
    await openTab('Method');
    expect(added()).toBe(2);
    act(() => window.history.back());
    await screen.findByRole('tab', { name: TABS.Compare, selected: true });
    act(() => window.history.back());
    await screen.findByRole('tab', { name: TABS.Simulate, selected: true });
    expect(screen.getByLabelText('Cost of one offer')).toHaveValue('80');
    expect(cutSlider()).toHaveAttribute('aria-valuetext', '0.40');
    act(() => window.history.forward());
    await screen.findByRole('tab', { name: TABS.Compare, selected: true });
  });

  it('move with the arrow keys, Home and End', async () => {
    await showSimulator();
    await figuresFor();
    const first = screen.getByRole('tab', { name: TABS.Simulate });
    act(() => first.focus());
    await userEvent.keyboard('{ArrowRight}');
    await screen.findByRole('tab', { name: TABS.Compare, selected: true });
    await userEvent.keyboard('{End}');
    await screen.findByRole('tab', { name: TABS.Method, selected: true });
    await userEvent.keyboard('{Home}');
    await screen.findByRole('tab', { name: TABS.Simulate, selected: true });
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: TABS.Simulate }));
  });

  it('ask the server nothing more, whatever is opened or pinned', async () => {
    const sweeps = requestsTo(SWEEP);
    const infos = requestsTo(MODEL_INFO);
    await showSimulator();
    await figuresFor();
    await press(screen.getByRole('button', { name: 'Pin these figures' }));
    await openTab('Compare');
    await openTab('Method');
    await openTab('Simulate');
    act(() => window.history.back());
    await screen.findByRole('tab', { name: TABS.Method, selected: true });
    await figuresFor();
    expect(sweeps).toHaveLength(1);
    expect(infos).toHaveLength(1);
  });
});

describe('the cut fader', () => {
  it('offers exactly the cuts the response sent, and steps only between them', async () => {
    await showSimulator('/simulator', [sweepApi((echo) => sparse(echo))]);
    await figuresFor();
    const slider = cutSlider();
    expect(slider).toHaveAttribute('max', '4');
    expect(slider).toHaveAttribute('aria-valuetext', '0.50');
    act(() => slider.focus());
    const seen: string[] = [];
    await userEvent.keyboard('{Home}');
    for (let i = 0; i < 5; i += 1) {
      seen.push(slider.getAttribute('aria-valuetext') ?? '');
      await userEvent.keyboard('{ArrowRight}');
    }
    expect(seen).toEqual(['0.10', '0.25', '0.36', '0.50', '0.80']);
    await userEvent.keyboard('{Shift>}{ArrowLeft}{/Shift}');
    expect(slider).toHaveAttribute('aria-valuetext', '0.10');
    await userEvent.keyboard('{End}');
    expect(slider).toHaveAttribute('aria-valuetext', '0.80');
    expect(address()).toEqual({ t: '0.80' });
  });

  it('takes a typed cut with a comma only when the API sent it, never the nearest', async () => {
    await showSimulator('/simulator', [sweepApi((echo) => sparse(echo))]);
    await figuresFor();
    const field = screen.getByRole('textbox', { name: 'Decision threshold value' });
    await userEvent.clear(field);
    await userEvent.type(field, '0,37{Enter}');
    expect(
      await screen.findByText(
        '0,37 is not one of the values on this scale. The nearest are 0.36 and 0.50.',
      ),
    ).toBeInTheDocument();
    expect(address()).toEqual({});
    await userEvent.clear(field);
    await userEvent.type(field, '0,25{Enter}');
    expect(address()).toEqual({ t: '0.25' });
    expect(cutSlider()).toHaveAttribute('aria-valuetext', '0.25');
  });

  it('shows no cut the address names that the response did not send', async () => {
    await showSimulator('/simulator?t=0.37', [sweepApi((echo) => sparse(echo))]);
    await figuresFor();
    // 0.37 is none of the five: the recommendation is shown, never a cut near 0.37.
    expect(cutSlider()).toHaveAttribute('aria-valuetext', '0.50');
    expect(screen.getByRole('heading', { name: 'At 0.50' })).toBeInTheDocument();
  });

  it('goes back to the recommendation from its reset chip, and follows it', async () => {
    await showSimulator('/simulator?t=0.40');
    await figuresFor();
    await press(screen.getByRole('button', { name: 'Reset Decision threshold to 0.54' }));
    expect(address()).toEqual({});
    expect(screen.getByText('Following the recommendation')).toBeInTheDocument();
    expect(document.activeElement).toBe(cutSlider());
  });

  it('marks the served cut on its scale', async () => {
    await showSimulator();
    await figuresFor();
    const control = screen.getByRole('region', { name: 'Scenario controls' });
    expect(within(control).getByText('0.36', { selector: 'text' })).toBeInTheDocument();
  });

  it('waits, unavailable but in place, until the sweep arrives', async () => {
    const { opened, release } = gate();
    await showSimulator('/simulator', [
      http.get(SWEEP, async ({ request }) => {
        await opened;
        return HttpResponse.json(sweepFor(echoOf(request)));
      }),
    ]);
    const slider = await screen.findByRole('slider', { name: 'Decision threshold' });
    // No cut to offer yet: off, and saying the cuts are coming.
    expect(slider).toBeDisabled();
    expect(screen.getByText('Loading the cuts.')).toBeInTheDocument();
    act(() => release());
    await figuresFor();
    expect(cutSlider()).toBeEnabled();
  });

  it('is busy, not disabled, while the next sweep loads', async () => {
    let seen = 0;
    const { opened, release } = gate();
    await showSimulator('/simulator', [
      http.get(SWEEP, async ({ request }) => {
        seen += 1;
        if (seen > 1) await opened;
        return HttpResponse.json(sweepFor(echoOf(request)));
      }),
    ]);
    await figuresFor();
    fireEvent.change(screen.getByLabelText('Cost of one offer'), { target: { value: '60' } });
    await waitFor(() => expect(cutSlider()).toHaveAttribute('aria-disabled', 'true'));
    expect(cutSlider()).toBeEnabled();
    expect(screen.getByText('Loading the cuts.')).toBeInTheDocument();
    act(() => release());
    await figuresFor(/^Figures for a \$60 offer/);
    expect(cutSlider()).not.toHaveAttribute('aria-disabled');
  });
});

describe('the figures at the cut', () => {
  it('are the response’s own, F1 included, in the readout and the results', async () => {
    await showSimulator('/simulator?t=0.40', [sweepApi((echo) => sweepFor(echo, { 40: AT_40 }))]);
    await figuresFor();
    expect(readout()).toHaveTextContent(
      'At 0.40: 512 flagged · precision 0.123 · recall 0.456 · F1 0.789.',
    );
    const results = screen.getByRole('region', { name: /^At / });
    const f1 = within(results).getByText('F1', { selector: 'dt' });
    expect(f1.nextElementSibling).toHaveTextContent('0.789');
    // Not F1 worked out from the precision and the recall shown.
    expect(document.body.textContent).not.toContain('0.194');
  });

  it('show no F1 where nothing is flagged', async () => {
    await showSimulator('/simulator?t=0.40', [
      sweepApi((echo) => sweepFor(echo, { 40: { ...AT_40, flagged: 0, precision: 0, f1: 0 } })),
    ]);
    await figuresFor();
    expect(readout()).toHaveTextContent('At 0.40: 0 flagged · precision — · recall 0.456 · F1 —.');
  });
});

describe('the Advanced controls', () => {
  const details = () =>
    screen.getByText('Advanced controls', { selector: 'span' }).closest('details');

  it('start closed, and name the values behind them', async () => {
    await showSimulator();
    await figuresFor();
    expect(details()).not.toHaveAttribute('open');
    expect(details()?.querySelector('summary')).toHaveTextContent(
      'Advanced controlsRevenue horizon 12 months · Gross margin 65%',
    );
    expect(within(details() ?? document.body).queryByText('CHANGED')).not.toBeInTheDocument();
  });

  it('say when a value behind them is changed', async () => {
    await showSimulator('/simulator?horizon=24');
    await figuresFor(/24-month horizon/);
    expect(details()).not.toHaveAttribute('open');
    expect(within(details() ?? document.body).getByText('CHANGED')).toBeInTheDocument();
  });

  it('open on arrival when a value behind them cannot be sent, which sends nothing', async () => {
    const asked = requestsTo(SWEEP);
    await showSimulator('/simulator?margin=0.955');
    await screen.findByText(/^Not sent: correct the marked field\./, { selector: 'p' });
    expect(details()).toHaveAttribute('open');
    expect(within(details() ?? document.body).getByText('needs correcting')).toBeInTheDocument();
    expect(screen.getByRole('slider', { name: 'Gross margin' })).toHaveAttribute(
      'aria-invalid',
      'true',
    );
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(asked).toEqual([]);
  });

  it('open on request, and send an edit made inside them', async () => {
    const asked = requestsTo(SWEEP);
    await showSimulator();
    await figuresFor();
    const summary = details()?.querySelector('summary');
    if (!summary) throw new Error('the summary');
    await userEvent.click(summary);
    expect(details()).toHaveAttribute('open');
    fireEvent.change(screen.getByLabelText('Revenue horizon'), { target: { value: '24' } });
    await figuresFor(/24-month horizon/);
    expect(asked.map((q) => q.get('horizon'))).toEqual(['12', '24']);
  });
});

describe('a decimal comma in a box', () => {
  it('is read as the point, sent as the number and written in the address with a point', async () => {
    const asked = requestsTo(SWEEP);
    await showSimulator();
    await figuresFor();
    fireEvent.change(screen.getByLabelText('Cost of one offer'), { target: { value: '12,50' } });
    await figuresFor(/^Figures for a \$12\.50 offer/);
    expect(asked.map((q) => q.get('cost'))).toEqual(['50', '12.5']);
    expect(address()).toEqual({ cost: '12.50' });
  });

  it.each(['1,234.5', '1,2,3'])(
    'refuses %j rather than read it as another number',
    async (text) => {
      const asked = requestsTo(SWEEP);
      await showSimulator();
      await figuresFor();
      fireEvent.change(screen.getByLabelText('Cost of one offer'), { target: { value: text } });
      expect(
        await screen.findByText(
          'The cost of one offer is a number, written in digits with a point or a comma for any decimals.',
        ),
      ).toBeInTheDocument();
      await new Promise((resolve) => setTimeout(resolve, 400));
      expect(asked).toHaveLength(1);
    },
  );
});

describe('the overview', () => {
  it('says when every assumption is at its default', async () => {
    await showSimulator();
    await figuresFor();
    const overview = screen.getByRole('region', { name: 'Scenario overview' });
    expect(overview).toHaveTextContent('Every assumption is at the served default.');
  });

  it('names each change against its served default, and the cut set by hand', async () => {
    await showSimulator('/simulator?cost=80&horizon=24&t=0.40');
    await figuresFor(/^Figures for a \$80 offer/);
    const changes = within(
      screen.getByRole('list', { name: 'Changed from the served defaults' }),
    ).getAllByRole('listitem');
    expect(changes.map((item) => item.textContent)).toEqual([
      'Cost of one offer $80 (default $50)',
      'Revenue horizon 24 months (default 12 months)',
      expect.stringMatching(/^Decision threshold 0\.40, set by hand \(recommended 0\.\d\d\)$/),
    ]);
  });
});

describe('Compare', () => {
  it('sets the served, recommended and selected cuts side by side, each from its own point', async () => {
    // Below the fixture's peak, so 0.40 stays a cut set by hand and 0.54 the recommendation.
    await showSimulator('/simulator?t=0.40&tab=compare', [
      sweepApi((echo) => sweepFor(echo, { 40: { ...AT_40, net_benefit: 24_721.5 } })),
    ]);
    const table = await screen.findByRole('table', { name: 'The response at three cuts' });
    const heads = within(table)
      .getAllByRole('columnheader')
      .map((head) => head.textContent);
    expect(heads).toEqual(['Served0.36', 'Recommended0.54', 'Selected0.40']);
    const row = (term: string) =>
      within(within(table).getByRole('rowheader', { name: term }).closest('tr') ?? table)
        .getAllByRole('cell')
        .map((cell) => cell.textContent);
    const fine = sweepFor(DEFAULTS);
    const served = fine.points[36];
    if (!served) throw new Error('point 36');
    expect(row('Flagged')).toEqual([String(served.flagged), expect.any(String), '512']);
    expect(row('F1')).toEqual([served.f1.toFixed(3), expect.any(String), '0.789']);
    expect(row('Net benefit')[2]).toBe('+$24,722');
    expect(row('Retained value')[2]).toBe('$70,322');
    // Side by side: no difference between two columns is printed.
    expect(table.textContent).not.toMatch(/Δ|difference|\bvs\.?\b/i);
    expect(
      screen.getByRole('region', { name: 'Served, recommended and selected' }),
    ).toHaveTextContent('nothing is subtracted from anything');
  });

  it('says when the served cut is not one of the cuts the API sent', async () => {
    await showSimulator('/simulator?tab=compare', [
      sweepApi((echo) => ({ ...sparse(echo), shipped_threshold: 0.33 })),
    ]);
    const table = await screen.findByRole('table', { name: 'The response at three cuts' });
    const served = within(table).getAllByText('not one of the cuts the API sent');
    expect(served).toHaveLength(8);
  });

  it('shows no figures under a served cut between the grid’s cuts, not even its neighbour’s', async () => {
    // 0.3613 prints as 0.36, a cut the sweep has: its figures are still not the served cut's.
    await showSimulator('/simulator?tab=compare', [
      sweepApi((echo) => ({ ...sweepFor(echo), shipped_threshold: 0.3613 })),
    ]);
    const table = await screen.findByRole('table', { name: 'The response at three cuts' });
    expect(within(table).getAllByText('not one of the cuts the API sent')).toHaveLength(8);
  });

  it('keeps the pins made on Simulate', async () => {
    await showSimulator();
    await figuresFor();
    await press(screen.getByRole('button', { name: 'Pin these figures' }));
    await openTab('Compare');
    const pins = screen.getByRole('table', { name: 'Pinned scenarios' });
    expect(within(pins).getAllByRole('button', { name: /^Remove/ })).toHaveLength(1);
    await openTab('Method');
    await openTab('Compare');
    expect(
      within(screen.getByRole('table', { name: 'Pinned scenarios' })).getAllByRole('button', {
        name: /^Remove/,
      }),
    ).toHaveLength(1);
  });
});

describe('Method', () => {
  it('says how the served cut was set, and what the API accepts, from the model card', async () => {
    await showSimulator('/simulator?tab=method');
    await figuresFor();
    const served = screen.getByRole('region', { name: 'The served cut-off' });
    expect(served).toHaveTextContent('0.36');
    expect(served).toHaveTextContent(/^The served cut-off0\.36highest threshold whose simulated/);
    const bounds = screen.getByRole('table', { name: 'Assumption bounds and defaults' });
    const rows = within(bounds)
      .getAllByRole('row')
      .slice(1)
      .map((row) => row.textContent);
    expect(rows).toEqual([
      'Cost of one offer$1 to $500$50',
      'Offer success rate0% to 80%30%',
      'Revenue horizon2 to 60 months12 months',
      'Gross margin10% to 90%65%',
    ]);
    expect(
      screen.getByRole('region', { name: 'Why not simply take the peak?' }),
    ).toBeInTheDocument();
  });

  it('reads the served cut from the model card, not the sweep', async () => {
    const info = modelInfo();
    await showSimulator('/simulator?tab=method', [
      http.get(MODEL_INFO, () => HttpResponse.json({ ...info, threshold: 0.41 })),
    ]);
    await figuresFor();
    expect(screen.getByRole('region', { name: 'The served cut-off' })).toHaveTextContent(
      /^The served cut-off0\.41/,
    );
  });
});

describe('the workspace', () => {
  it('reads in the order a phone shows it: controls, results, the rest of the controls, the overview', async () => {
    await showSimulator();
    await figuresFor();
    const order = [
      screen.getByRole('region', { name: 'Scenario controls' }),
      screen.getByRole('region', { name: /^At / }),
      screen.getByRole('region', { name: 'Advanced controls' }),
      screen.getByRole('region', { name: 'Scenario overview' }),
    ];
    order.slice(1).forEach((later, i) => {
      const earlier = order[i];
      if (!earlier) throw new Error('an earlier region');
      expect(
        earlier.compareDocumentPosition(later) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });
  });

  it('says nothing of following the recommendation before there are cuts', async () => {
    const { opened, release } = gate();
    await showSimulator('/simulator?t=0.40', [
      http.get(SWEEP, async ({ request }) => {
        await opened;
        return HttpResponse.json(sweepFor(echoOf(request)));
      }),
    ]);
    await screen.findByRole('slider', { name: 'Decision threshold' });
    expect(screen.queryByText('Following the recommendation')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Use recommended' })).not.toBeInTheDocument();
    act(() => release());
    await figuresFor();
    expect(screen.getByText('Set by hand')).toBeInTheDocument();
  });

  it('takes a rate typed as the fader shows it, with its percent sign', async () => {
    await showSimulator();
    await figuresFor();
    const field = screen.getByRole('textbox', { name: 'Offer success rate value' });
    await userEvent.clear(field);
    await userEvent.type(field, '40%{Enter}');
    await waitFor(() => expect(address()).toEqual({ success: '0.4' }));
    await figuresFor(/40% success/);
  });

  it('gives the panel the focus when Back takes away the tab holding it', async () => {
    await showSimulator();
    await figuresFor();
    await openTab('Compare');
    const pin = screen.getByRole('button', { name: 'Pin these figures' });
    act(() => pin.focus());
    act(() => window.history.back());
    await screen.findByRole('tab', { name: TABS.Simulate, selected: true });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('tabpanel')));
  });

  it('leaves the focus alone when a tab is clicked in a browser that does not focus the button', async () => {
    await showSimulator();
    await figuresFor();
    act(() => screen.getByLabelText('Cost of one offer').focus());
    // A click with no focus change, as Safari makes it.
    fireEvent.click(screen.getByRole('tab', { name: TABS.Compare }));
    await screen.findByRole('tab', { name: TABS.Compare, selected: true });
    expect(document.activeElement).not.toBe(screen.getByRole('tabpanel'));
  });

  it('sets no style attribute anywhere on any tab, hovered chart included', async () => {
    const { container } = await showSimulator('/simulator?t=0.40');
    await figuresFor();
    const capture = container.querySelector('[class*="capture"]');
    if (!capture) throw new Error('the chart’s capture layer');
    fireEvent.pointerMove(capture, { clientX: 10, clientY: 10 });
    for (const tab of ['Simulate', 'Compare', 'Method'] as const) {
      await openTab(tab);
      expect(container.querySelectorAll('[style]')).toHaveLength(0);
    }
  });
});

describe('a fader with no value held', () => {
  /** The fader's drawn scale, beside its range. */
  const scaleOf = (slider: HTMLElement) => slider.parentElement?.querySelector('svg');

  it('shows a rate it cannot place as no value, never as its first percent', async () => {
    await showSimulator('/simulator?success=0.305');
    await screen.findByText(/^Not sent: correct the marked field\./, { selector: 'p' });
    const rate = screen.getByRole('slider', { name: 'Offer success rate' });
    expect(rate).toHaveAttribute('data-empty', 'true');
    expect(rate).toHaveAttribute('aria-valuetext', 'no value');
    expect(scaleOf(rate)).toHaveTextContent('no value chosen');
    // A whole percent chosen from the keyboard brings the thumb back.
    act(() => rate.focus());
    await userEvent.keyboard('{Home}');
    expect(rate).not.toHaveAttribute('data-empty');
    expect(rate).toHaveAttribute('aria-valuetext', '0%');
  });

  it('holds the cut empty and off while the first sweep loads', async () => {
    const { opened, release } = gate();
    await showSimulator('/simulator', [
      http.get(SWEEP, async ({ request }) => {
        await opened;
        return HttpResponse.json(sweepFor(echoOf(request)));
      }),
    ]);
    const slider = await screen.findByRole('slider', { name: 'Decision threshold' });
    expect(slider).toBeDisabled();
    expect(slider).toHaveAttribute('data-empty', 'true');
    act(() => release());
    await figuresFor();
    expect(cutSlider()).not.toHaveAttribute('data-empty');
    expect(cutSlider()).toHaveAttribute('aria-valuetext', '0.54');
  });

  it('holds the cut empty and off when the response names no cut of its own, and picks none', async () => {
    await showSimulator('/simulator', [
      sweepApi((echo) => {
        const sweep = sweepFor(echo);
        return { ...sweep, recommended: { ...sweep.recommended, threshold: 1.5 } };
      }),
    ]);
    await screen.findByText(
      'No cut can be chosen: the sweep has no point at its own recommendation.',
    );
    const slider = cutSlider();
    expect(slider).toBeDisabled();
    expect(slider).toHaveAttribute('data-empty', 'true');
    expect(slider).toHaveAttribute('aria-valuetext', 'no value');
    // Not the first cut the response sent, nor any other.
    expect(scaleOf(slider)).not.toHaveTextContent(/\d\.\d\d/);
    expect(screen.getByRole('textbox', { name: 'Decision threshold value' })).toHaveValue('');
  });

  it('keeps the cut’s thumb whenever the response gives it a cut', async () => {
    await showSimulator('/simulator?t=0.40');
    await figuresFor();
    expect(cutSlider()).not.toHaveAttribute('data-empty');
    expect(screen.getByRole('slider', { name: 'Offer success rate' })).not.toHaveAttribute(
      'data-empty',
    );
  });
});

describe('the words on every tab', () => {
  it('never claim a retention, a saving, a cause or a ratio of their own', async () => {
    await showSimulator('/simulator?t=0.40');
    await figuresFor();
    for (const tab of ['Simulate', 'Compare', 'Method'] as const) {
      await openTab(tab);
      const text = document.body.textContent;
      expect(text).not.toMatch(/\b(saved?|saves|rescued?|prevented|guarantee[sd]?)\b/i);
      expect(text).not.toMatch(/\b(causes|caused by|drives|driven by|will prevent)\b/i);
      expect(text).not.toMatch(/\b\d+ in \d+\b/);
      expect(text).not.toMatch(/\d×/);
    }
  });
});
