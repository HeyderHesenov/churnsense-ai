import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw/http';
import type { Segments } from '../../api/queries';
import { App } from '../../app/App';
import { MAIN_ID } from '../../components/shell/Shell';
import { entriesAdded } from '../../test/history';
import { contractAlone, contractByTenure, patternsApi } from '../../test/patternsApi';
import { renderAt } from '../../test/render';
import { recordRequests, refuse, server } from '../../test/server';
import { ASSOCIATIONS, FAILED, RANKED, TITLE, WITHHELD } from './copy';

/** The page at ``path`` with the fixture API; ``handlers`` go first, so they answer. */
async function showPatterns(path = '/patterns', handlers: Parameters<typeof server.use> = []) {
  server.use(...handlers, ...patternsApi());
  const rendered = renderAt(<App />, path);
  await screen.findByRole('heading', { level: 1, name: TITLE });
  await screen.findByRole('table');
  return rendered;
}

const user = () => userEvent.setup({ delay: null });
const table = () => screen.getByRole('table');
const cell = (name: string) =>
  within(table()).getByRole('button', { name: (label) => label.startsWith(name) });
const segmentRequests = (seen: URL[]) =>
  seen.filter((u) => u.pathname === '/api/segments').map((u) => u.search);
const ranked = () => screen.getByRole('region', { name: RANKED.title });
const associations = () => screen.getByRole('region', { name: ASSOCIATIONS.title });

/** A hold on the breakdown for ``rows``: answered with ``body`` only when released. */
function holdSegments(rows: string, body: Segments) {
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => (release = resolve));
  const handler = http.get('/api/segments', async ({ request }) => {
    if (new URL(request.url).searchParams.get('rows') !== rows) return undefined;
    await gate;
    return HttpResponse.json(body);
  });
  return { handler, release: () => act(release) };
}

describe('Patterns: the breakdown', () => {
  it('crosses contract with tenure by default, from the training partition', async () => {
    const seen = recordRequests();
    await showPatterns();
    expect(segmentRequests(seen)).toEqual(['?rows=Contract&cols=tenure']);
    expect(
      screen.getByText(
        'Training partition · 4,000 of 7,000 customers · 1,000 churned · 25.0% observed churn',
      ),
    ).toBeInTheDocument();
    const headers = within(table()).getAllByRole('columnheader');
    expect(headers.map((h) => h.textContent)).toEqual([
      'All25.0%',
      '0–31,000 · 42.0%',
      '4–723,000 · 19.3%',
    ]);
    expect(within(table()).getByRole('rowheader', { name: /One year/ })).toHaveTextContent(
      'One year1,000 · 15.0%',
    );
  });

  it('shows a reported group with its rate and size', async () => {
    await showPatterns();
    const reported = cell('Contract One year, tenure 4–72 months');
    expect(reported).toHaveTextContent('15.0%n 988');
    expect(reported).toHaveAccessibleName(
      'Contract One year, tenure 4–72 months: 988 customers, 148 churned. 15.0% observed churn ' +
        '(95% interval 11.9–17.9%), 0.60× the 25.0% of all 4,000 training customers, these included.',
    );
  });

  it('never shows a hidden group’s churn, only its size and why', async () => {
    await showPatterns();
    expect(cell('Contract One year, tenure 0–3 months')).toHaveTextContent('—n<20');
    expect(cell('Contract Month-to-month, tenure 0–3 months')).toHaveTextContent('—held back');
    expect(cell('Contract Two year, tenure 0–3 months')).toHaveTextContent('—none');
    // The hidden groups' churn appears nowhere on the page: not in a cell, the ranking or a name.
    const main = screen.getByRole('main');
    for (const name of ['Month-to-month, tenure 0–3', 'One year, tenure 0–3']) {
      expect(within(ranked()).queryByText((text) => text.includes(name))).toBeNull();
      expect(cell(`Contract ${name}`).getAttribute('aria-label')).not.toMatch(/churned|%/);
    }
    expect(main).not.toHaveTextContent(/probabilit|risk band|predicted/i);
  });

  it('shows the size of every group, hidden ones too, on the customers measure', async () => {
    await showPatterns('/patterns?show=customers');
    expect(cell('Contract One year, tenure 0–3 months')).toHaveTextContent('12n 12');
    expect(cell('Contract One year, tenure 4–72 months')).toHaveTextContent('988n 988');
  });

  it('switches the measure in place, without a history entry', async () => {
    await showPatterns();
    const added = entriesAdded();
    await user().click(screen.getByRole('button', { name: 'Lift' }));
    expect(window.location.search).toBe('?show=lift');
    expect(added()).toBe(0);
    expect(cell('Contract One year, tenure 4–72 months')).toHaveTextContent('0.60×');
    expect(cell('Contract One year, tenure 0–3 months')).toHaveTextContent('—n<20');
  });
});

describe('Patterns: choosing a view', () => {
  it('asks for the rows and columns chosen and keeps them in the address', async () => {
    const seen = recordRequests();
    await showPatterns();
    const added = entriesAdded();
    await user().selectOptions(screen.getByRole('combobox', { name: 'Columns' }), 'PaymentMethod');
    await waitFor(() =>
      expect(segmentRequests(seen)).toContain('?rows=Contract&cols=PaymentMethod'),
    );
    expect(window.location.search).toBe('?cols=PaymentMethod');
    expect(added()).toBe(1);
  });

  it('breaks churn down by one attribute when the columns are none', async () => {
    const seen = recordRequests();
    await showPatterns('/patterns?cols=none');
    expect(segmentRequests(seen)).toEqual(['?rows=Contract']);
    expect(within(table()).getAllByRole('columnheader')[1]).toHaveTextContent('Churn rate');
    expect(cell('Contract Two year')).toHaveTextContent('5.0%n 1,000');
    expect(screen.getByRole('button', { name: 'Swap rows and columns' })).toBeDisabled();
  });

  it('swaps rows and columns', async () => {
    const seen = recordRequests();
    await showPatterns();
    await user().click(screen.getByRole('button', { name: 'Swap rows and columns' }));
    await waitFor(() => expect(segmentRequests(seen)).toContain('?rows=tenure&cols=Contract'));
    expect(window.location.search).toBe('?rows=tenure&cols=Contract');
  });

  it('opens a suggested view and marks the one shown', async () => {
    const seen = recordRequests();
    await showPatterns();
    const views = screen.getByRole('group', { name: 'Suggested views' });
    expect(within(views).getByRole('button', { name: 'Contract × tenure' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await user().click(within(views).getByRole('button', { name: 'Internet × payment' }));
    await waitFor(() =>
      expect(segmentRequests(seen)).toContain('?rows=InternetService&cols=PaymentMethod'),
    );
  });

  it('never offers or asks for a dimension crossed with itself', async () => {
    const seen = recordRequests();
    await showPatterns('/patterns?rows=tenure&cols=tenure');
    expect(segmentRequests(seen)).toEqual(['?rows=tenure']);
    const columns = screen.getByRole('combobox', { name: 'Columns' });
    expect(within(columns).queryByRole('option', { name: 'Tenure (months)' })).toBeNull();
  });

  it('reads an address it does not understand as the default view', async () => {
    const seen = recordRequests();
    await showPatterns('/patterns?rows=gender&cols=Churn&show=probability');
    expect(segmentRequests(seen)).toEqual(['?rows=Contract&cols=tenure']);
  });

  it('keeps the last view, labelled as it is, while the next one loads', async () => {
    const hold = holdSegments('InternetService', contractByTenure({ rows: 'InternetService' }));
    await showPatterns('/patterns', [hold.handler]);
    await user().selectOptions(screen.getByRole('combobox', { name: 'Rows' }), 'InternetService');
    expect(
      await screen.findByText('Showing Contract × tenure while Internet × tenure loads.', {
        selector: '[role="status"]',
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Breakdown' })).toHaveAttribute('aria-busy', 'true');
    expect(within(table()).getByRole('rowheader', { name: /Month-to-month/ })).toBeInTheDocument();
    hold.release();
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'Breakdown' })).toHaveAttribute(
        'aria-busy',
        'false',
      ),
    );
  });
});

describe('Patterns: pinning a group', () => {
  it('pins a group, links its training customers in the explorer, and lets go on Escape', async () => {
    await showPatterns();
    const group = cell('Contract One year, tenure 4–72 months');
    await user().click(group);
    expect(group).toHaveAttribute('aria-pressed', 'true');
    expect(window.location.search).toBe('?pr=One+year&pc=4-72');
    const link = screen.getByRole('link', {
      name: 'Open these 988 training customers in the customer explorer',
    });
    expect(link).toHaveAttribute(
      'href',
      '/explorer?contract=One+year&tenure_min=4&tenure_max=72&part=train',
    );
    expect(screen.getByRole('status')).toHaveTextContent(/^Pinned: Contract One year/);

    await user().keyboard('{Escape}');
    expect(group).toHaveAttribute('aria-pressed', 'false');
    expect(window.location.search).toBe('');
    expect(screen.queryByRole('link', { name: /Open these/ })).toBeNull();
  });

  it('says why a hidden group does not open in the explorer', async () => {
    await showPatterns('/patterns?pr=Month-to-month&pc=0-3');
    expect(
      screen.getByText('A group whose churn is hidden does not open in the explorer.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Open these/ })).toBeNull();
  });

  it('says which dimension the explorer cannot filter', async () => {
    await showPatterns('/patterns?rows=monthly&cols=StreamingTV&pr=%3C%2435&pc=No');
    expect(screen.getByText('The explorer cannot filter by monthly charges.')).toBeInTheDocument();
  });

  it('pins from the ranking, the same group as the table', async () => {
    await showPatterns();
    await user().click(
      within(ranked()).getByRole('button', { name: /^Contract Two year, tenure 4–72 months/ }),
    );
    expect(cell('Contract Two year, tenure 4–72 months')).toHaveAttribute('aria-pressed', 'true');
    await user().click(
      within(ranked()).getByRole('button', { name: /^Contract Two year, tenure 4–72 months/ }),
    );
    expect(cell('Contract Two year, tenure 4–72 months')).toHaveAttribute('aria-pressed', 'false');
  });

  it('lets go of the pin when the view changes', async () => {
    await showPatterns('/patterns?pr=One+year&pc=4-72');
    expect(cell('Contract One year, tenure 4–72 months')).toHaveAttribute('aria-pressed', 'true');
    await user().selectOptions(screen.getByRole('combobox', { name: 'Columns' }), 'none');
    await waitFor(() => expect(window.location.search).toBe('?cols=none'));
  });

  it('ignores a pin the shown view does not hold', async () => {
    await showPatterns('/patterns?pr=One+year&pc=13-24');
    expect(within(table()).queryByRole('button', { pressed: true })).toBeNull();
    expect(screen.queryByRole('link', { name: /Open these/ })).toBeNull();
  });
});

describe('Patterns: the reader keeps their place', () => {
  it('keeps focus on an association after the breakdown it opens arrives', async () => {
    await showPatterns();
    const u = user();
    await u.click(within(associations()).getByRole('button', { name: /^Contract/ }));
    await waitFor(() =>
      expect(within(table()).getAllByRole('columnheader')[1]).toHaveTextContent('Churn rate'),
    );
    const button = within(associations()).getByRole('button', { name: /^Contract/ });
    expect(button).toHaveAttribute('aria-pressed', 'true');
    expect(document.activeElement).toBe(button);
  });

  it('announces the view shown, a pin and its release, outside the busy region', async () => {
    await showPatterns();
    const status = screen.getByRole('status');
    expect(screen.getByRole('region', { name: 'Breakdown' })).not.toContainElement(status);
    expect(status).toHaveTextContent('Showing Contract × tenure.');
    await user().click(cell('Contract One year, tenure 4–72 months'));
    expect(status).toHaveTextContent(/^Pinned: Contract One year/);
    await user().keyboard('{Escape}');
    expect(status).toHaveTextContent('Showing Contract × tenure.');
  });

  it('lets go of a pin from the ranking with Escape too', async () => {
    await showPatterns('/patterns?pr=Two+year&pc=4-72');
    within(ranked())
      .getByRole('button', { name: /^Contract Two year, tenure 4–72 months/ })
      .focus();
    await user().keyboard('{Escape}');
    expect(window.location.search).toBe('');
  });

  it('keeps the pinned group when rows and columns swap', async () => {
    await showPatterns('/patterns?pr=One+year&pc=4-72');
    await user().click(screen.getByRole('button', { name: 'Swap rows and columns' }));
    expect(window.location.search).toBe('?rows=tenure&cols=Contract&pr=4-72&pc=One+year');
  });
});

describe('Patterns: the ranking and the associations', () => {
  it('ranks the highest reported groups with their intervals and sizes', async () => {
    await showPatterns();
    const rows = within(ranked()).getAllByRole('button');
    expect(rows.map((r) => r.textContent)).toEqual([
      'Contract Month-to-month, tenure 4–72 months26.9% 23.8–29.8% · n 1,040',
      'Contract One year, tenure 4–72 months15.0% 11.9–17.9% · n 988',
      'Contract Two year, tenure 4–72 months5.0% 1.9–7.9% · n 1,000',
    ]);
    expect(ranked()).toHaveTextContent(
      'The highest of 3 reported groups, 20 or more customers each.',
    );
  });

  it('opens an attribute the page breaks churn down by, and only those', async () => {
    const seen = recordRequests();
    await showPatterns();
    const list = associations();
    expect(within(list).queryByText(/gender/i)).toBeNull();
    expect(within(list).queryByRole('button', { name: /Online backup/ })).toBeNull();
    expect(within(list).getByText(/Online backup/)).toBeInTheDocument();
    await user().click(within(list).getByRole('button', { name: /^Contract/ }));
    await waitFor(() => expect(segmentRequests(seen)).toContain('?rows=Contract'));
    expect(window.location.search).toBe('?cols=none');
  });

  it('says so when the association ranking is not available', async () => {
    await showPatterns('/patterns', [
      http.get('/api/segments', () => HttpResponse.json(contractByTenure({ association: null }))),
    ]);
    expect(within(associations()).getByText(ASSOCIATIONS.unavailable)).toBeInTheDocument();
  });

  it('says so when no group has enough customers for a rate', async () => {
    await showPatterns('/patterns', [
      http.get('/api/segments', () => HttpResponse.json(contractAlone({ ranked: [] }))),
    ]);
    expect(within(ranked()).getByText(RANKED.empty)).toBeInTheDocument();
  });
});

describe('Patterns: the states of the page', () => {
  it('waits for the breakdown with a skeleton', async () => {
    const hold = holdSegments('Contract', contractByTenure());
    server.use(hold.handler, ...patternsApi());
    renderAt(<App />, '/patterns');
    expect(await screen.findByText('Loading the breakdown')).toBeInTheDocument();
    hold.release();
    await screen.findByRole('table');
  });

  it('offers a retry that keeps the reader on the page when the breakdown fails', async () => {
    const seen = recordRequests();
    server.use(refuse('get', '/api/segments', 500, 'internal_error'));
    renderAt(<App />, '/patterns');
    expect(await screen.findByText(FAILED, {}, { timeout: 3000 })).toBeInTheDocument();
    server.resetHandlers();
    server.use(...patternsApi());
    await user().click(screen.getByRole('button', { name: /Try again/ }));
    await screen.findByRole('table');
    expect(document.activeElement).toBe(document.getElementById(MAIN_ID));
    expect(segmentRequests(seen).length).toBeGreaterThanOrEqual(3);
  });

  it('says outcomes are withheld, and asks no more', async () => {
    const seen = recordRequests();
    server.use(refuse('get', '/api/segments', 503, 'outcomes_withheld'));
    renderAt(<App />, '/patterns');
    expect(await screen.findByText(WITHHELD.title)).toBeInTheDocument();
    expect(screen.getByText(WITHHELD.body)).toBeInTheDocument();
    expect(segmentRequests(seen)).toHaveLength(1);
  });
});

describe('Patterns: what each state of a group shows', () => {
  it('pins exactly one group, without a history entry for the pin or its release', async () => {
    await showPatterns();
    const added = entriesAdded();
    await user().click(cell('Contract Two year, tenure 4–72 months'));
    expect(within(table()).getAllByRole('button', { pressed: true })).toHaveLength(1);
    await user().keyboard('{Escape}');
    expect(added()).toBe(0);
  });

  it('marks the pinned group in the ranking too', async () => {
    await showPatterns('/patterns?pr=One+year&pc=4-72');
    const rows = within(ranked()).getAllByRole('button');
    expect(rows.map((r) => r.getAttribute('aria-pressed'))).toEqual(['false', 'true', 'false']);
  });

  it('shows no pin and no explorer link while another view loads', async () => {
    const hold = holdSegments('InternetService', contractByTenure({ rows: 'InternetService' }));
    await showPatterns('/patterns?pr=One+year&pc=4-72', [hold.handler]);
    expect(screen.getByRole('link', { name: /Open these/ })).toBeInTheDocument();
    act(() => {
      window.history.pushState(null, '', '/patterns?rows=InternetService&pr=One+year&pc=4-72');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await screen.findByText(/while Internet × tenure loads/, { selector: '[role="status"]' });
    expect(screen.queryByRole('link', { name: /Open these/ })).toBeNull();
    expect(within(table()).queryByRole('button', { pressed: true })).toBeNull();
    expect(table().closest('[inert]')).not.toBeNull();
    expect(within(ranked()).getAllByRole('button')[0]?.closest('[inert]')).not.toBeNull();
    hold.release();
    await waitFor(() => expect(table().closest('[inert]')).toBeNull());
  });

  it('shades lift either side of 1× by its distance on a log scale', async () => {
    await showPatterns('/patterns?show=lift');
    const below = cell('Contract One year, tenure 4–72 months');
    expect(below).toHaveAttribute('data-side', 'below');
    const lift = 148 / 988 / 0.25;
    expect(Number(below.style.getPropertyValue('--t'))).toBeCloseTo(
      -Math.log(lift) / Math.log(5),
      6,
    );
    expect(cell('Contract Month-to-month, tenure 4–72 months')).toHaveAttribute(
      'data-side',
      'above',
    );
  });

  it('describes the group pointed at over the pinned one, then the pin again', async () => {
    await showPatterns('/patterns?pr=One+year&pc=4-72');
    const line = () => screen.getByText(/^(Pinned · )?Contract /, { selector: 'p' }).textContent;
    expect(line()).toMatch(/^Pinned · Contract One year/);
    fireEvent.pointerEnter(cell('Contract Two year, tenure 4–72 months'));
    expect(line()).toMatch(/^Contract Two year, tenure 4–72 months: 1,000/);
    fireEvent.pointerLeave(cell('Contract Two year, tenure 4–72 months'));
    expect(line()).toMatch(/^Pinned · Contract One year/);
  });
});

describe('Patterns: a new response starts clear', () => {
  it('forgets the group pointed at when the next view arrives', async () => {
    const seen = recordRequests();
    await showPatterns();
    fireEvent.pointerEnter(cell('Contract One year, tenure 4–72 months'));
    expect(screen.getByText(/^Contract One year, tenure 4–72 months: 988/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Columns' }), {
      target: { value: 'PaymentMethod' },
    });
    await waitFor(() =>
      expect(segmentRequests(seen)).toContain('?rows=Contract&cols=PaymentMethod'),
    );
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'Breakdown' })).toHaveAttribute(
        'aria-busy',
        'false',
      ),
    );
    expect(
      screen.getByText(/^Overall observed churn in the training partition/),
    ).toBeInTheDocument();
  });

  it('marks the association asked for while its breakdown loads', async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    const hold = http.get('/api/segments', async ({ request }) => {
      if (new URL(request.url).searchParams.has('cols')) return undefined;
      await gate;
      return HttpResponse.json(contractAlone());
    });
    await showPatterns('/patterns', [hold]);
    await user().click(within(associations()).getByRole('button', { name: /^Contract/ }));
    await screen.findByText(/while Contract loads/, { selector: '[role="status"]' });
    expect(within(associations()).getByRole('button', { name: /^Contract/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    act(release);
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'Breakdown' })).toHaveAttribute(
        'aria-busy',
        'false',
      ),
    );
  });
});
