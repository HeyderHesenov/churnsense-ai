import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { HttpHandler } from 'msw';
import { http, HttpResponse } from 'msw/http';
import type { Schemas } from '../../api/client';
import { App } from '../../app/App';
import { explorerApi, listing } from '../../test/explorerApi';
import { entriesAdded } from '../../test/history';
import { press } from '../../test/keyboard';
import { modelInfo } from '../../test/fixtures';
import { renderAt } from '../../test/render';
import { answer, recordRequests, refuse, server } from '../../test/server';

type ApiOptions = Parameters<typeof explorerApi>[0];

/**
 * The page at ``path``, its API answering from the 37-customer fixture book;
 * ``before`` answers first (a request the page makes as it mounts included).
 */
function showExplorer(path = '/explorer', options: ApiOptions = {}, before: HttpHandler[] = []) {
  server.use(...explorerApi(options));
  if (before.length > 0) server.use(...before);
  return renderAt(<App />, path);
}

const FAILED: Schemas['ErrorResponse'] = {
  error: 'internal_error',
  detail: 'Refused by a test.',
  problems: [],
};

/** Every gate still shut, opened after each test whatever it did. */
const gates: (() => void)[] = [];
afterEach(() => {
  for (const open of gates.splice(0)) open();
});

/**
 * The listing, answered from the book, except that the requests ``held``
 * matches wait until ``release`` (and then fail, with ``fail``).
 */
function gate(held: (url: URL) => boolean, { fail = false } = {}) {
  let release: () => void = () => undefined;
  const opened = new Promise<void>((resolve) => (release = resolve));
  gates.push(() => release());
  const handler = http.get('/api/customers', async ({ request }) => {
    const url = new URL(request.url);
    if (held(url)) {
      await opened;
      if (fail) return HttpResponse.json(FAILED, { status: 500 });
    }
    return HttpResponse.json(listing(url));
  });
  return { handler, release: () => release() };
}

/** The listing, failing every request ``refused`` matches. */
function failing(refused: (url: URL) => boolean) {
  return http.get('/api/customers', ({ request }) => {
    const url = new URL(request.url);
    return refused(url)
      ? HttpResponse.json(FAILED, { status: 500 })
      : HttpResponse.json(listing(url));
  });
}

/** A listing page's request, not a selection's (one row). */
function isPage(url: URL) {
  return url.searchParams.get('page_size') !== '1';
}

async function loaded() {
  return screen.findByRole('heading', { level: 1, name: 'Every customer, scored and filterable' });
}

/** The table once its rows are in. */
async function table() {
  const found = await screen.findByRole('table', { name: 'Customers' });
  await within(found).findAllByRole('button', { name: /^DEMO-/ });
  return found;
}

function rowButtons(found: HTMLElement) {
  return within(found).getAllByRole('button', { name: /^DEMO-/ });
}

/** Listing requests only (a selection, a preset's count included, asks for one row). */
function listings(seen: URL[]) {
  return seen.filter((url) => url.pathname === '/api/customers' && isPage(url));
}

function summaryTile(label: RegExp | string) {
  const summary = screen.getByRole('region', { name: 'Selection summary' });
  const name = within(summary).getByText(label);
  const tile = name.parentElement;
  if (!tile) throw new Error('tile');
  return tile;
}

function selectionStatus() {
  return within(screen.getByRole('region', { name: 'Selection summary' })).getByRole('status');
}

function search() {
  return new URLSearchParams(window.location.search);
}

function customers() {
  return screen.getByRole('region', { name: 'Customers' });
}

function detail() {
  return screen.getByRole('complementary', { name: 'Customer detail' });
}

function next() {
  return within(detail()).getByRole('button', { name: 'Next customer in the list' });
}

function previous() {
  return within(detail()).getByRole('button', { name: 'Previous customer in the list' });
}

const still = () => new Promise((resolve) => setTimeout(resolve, 300));

describe('Explorer: loading the book', () => {
  it('asks for the first fifteen customers, riskiest first, and says what it shows', async () => {
    const seen = recordRequests();
    showExplorer();
    await loaded();
    expect(
      await screen.findByText(
        "All 37 rows with their served probability. Outcomes appear for train and validation customers only; the test partition's are withheld.",
      ),
    ).toBeInTheDocument();
    const found = await table();
    const [first] = listings(seen);
    expect(first?.searchParams.get('page_size')).toBe('15');
    expect(first?.searchParams.get('sort')).toBe('probability');
    expect(first?.searchParams.get('dir')).toBe('desc');
    expect(first?.searchParams.get('page')).toBe('1');
    expect(first?.searchParams.getAll('bands')).toEqual([]);
    expect(rowButtons(found)).toHaveLength(15);
    expect(rowButtons(found)[0]).toHaveAccessibleName(/^DEMO-0001 /);
    expect(
      screen.getByText('Showing 1–15 of 37 · select a row to open its profile and explanation'),
    ).toBeInTheDocument();
    expect(within(summaryTile('Customers')).getByText('37')).toBeInTheDocument();
    expect(within(summaryTile('Customers')).getByText('100.0% of the book')).toBeInTheDocument();
    expect(within(summaryTile('Flagged at 0.36')).getByText('24')).toBeInTheDocument();
    expect(await screen.findByText('37 customers match, 24 flagged.')).toHaveAttribute(
      'role',
      'status',
    );
  });

  it('opens on the flagged customers when the overview links here', async () => {
    const seen = recordRequests();
    showExplorer('/explorer?flagged=true');
    await table();
    const flag = screen.getByRole('group', { name: 'Decision at 0.36' });
    expect(within(flag).getByRole('button', { name: 'Flagged' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(listings(seen)[0]?.searchParams.get('flagged')).toBe('true');
    expect(
      screen.getByText('Showing 1–15 of 24 · select a row to open its profile and explanation'),
    ).toBeInTheDocument();
  });

  it('takes whether outcomes are withheld from the book’s own answer', async () => {
    showExplorer('/explorer', { withheld: 'Retrain with make train.' });
    await table();
    expect(
      await screen.findByText(
        'All 37 rows with their served probability. Observed outcomes are withheld: Retrain with make train.',
      ),
    ).toBeInTheDocument();
    expect(within(summaryTile('Observed churn')).getByText('Withheld')).toBeVisible();
  });

  it('says nothing about outcomes until the API has said whether they are withheld', async () => {
    const whole = gate((url) => !isPage(url) && [...url.searchParams.keys()].length === 1);
    showExplorer('/explorer', {}, [
      whole.handler,
      refuse('get', '/api/model-info', 500, 'internal_error'),
    ]);
    await table();
    expect(screen.getByText('All … rows with their served probability.')).toBeInTheDocument();
    whole.release();
    expect(
      await screen.findByText(
        "All 37 rows with their served probability. Outcomes appear for train and validation customers only; the test partition's are withheld.",
      ),
    ).toBeInTheDocument();
  });

  it('keeps the page a link opens on', async () => {
    showExplorer('/explorer?page=2');
    await table();
    await act(still);
    expect(window.location.search).toBe('?page=2');
    expect(screen.getByText(/^Showing 16–30 of 37/)).toBeInTheDocument();
  });

  it('gives the selection’s figures, every one the API’s', async () => {
    showExplorer();
    await table();
    await waitFor(() =>
      expect(within(summaryTile('Mean probability')).getByText('0.500')).toBeInTheDocument(),
    );
    expect(within(summaryTile('Mean probability')).getByText('expected churners 19')).toBeVisible();
    expect(within(summaryTile('Flagged at 0.36')).getByText('64.9% of selection')).toBeVisible();
    expect(within(summaryTile('Observed churn')).getByText('53.3%')).toBeVisible();
    expect(within(summaryTile('Observed churn')).getByText('16 of 30 train + val')).toBeVisible();
    expect(within(summaryTile('Monthly charges')).getByText('$2,198')).toBeVisible();
    expect(within(summaryTile('Monthly charges')).getByText('$1,065 / mo at risk')).toBeVisible();
  });

  it('gives no observed rate for fewer than 20 customers with outcomes', async () => {
    showExplorer('/explorer?contract=One+year');
    await table();
    await waitFor(() =>
      expect(within(summaryTile('Customers')).getByText('12')).toBeInTheDocument(),
    );
    expect(within(summaryTile('Customers')).getByText('32.4% of the book')).toBeVisible();
    expect(within(summaryTile('Observed churn')).getByText('—')).toBeVisible();
    expect(
      within(summaryTile('Observed churn')).getByText('fewer than 20 with outcomes'),
    ).toBeVisible();
  });

  it('says the test partition’s outcomes are withheld', async () => {
    showExplorer('/explorer?part=test');
    await table();
    await waitFor(() =>
      expect(
        within(summaryTile('Observed churn')).getByText('test outcomes withheld'),
      ).toBeVisible(),
    );
  });

  it('draws the selection’s probability spread in the served bands, with its figures as a table', async () => {
    showExplorer();
    await table();
    const chart = await screen.findByRole('img', {
      name: 'Histogram of probability for the selection',
    });
    const figures = screen.getByRole('table', { name: 'Customers per 0.05 of probability' });
    expect(within(figures).getAllByRole('row')).toHaveLength(21);
    expect(within(figures).getByText('0.95–1.00').closest('tr')).toHaveTextContent(
      /^0\.95–1\.001$/,
    );
    expect(within(figures).getByText('0.40–0.45').closest('tr')).toHaveTextContent(
      /^0\.40–0\.452$/,
    );
    const bars = [...chart.querySelectorAll('rect[data-band]')].map((bar) =>
      bar.getAttribute('data-band'),
    );
    expect(bars).toHaveLength(20);
    for (const band of ['Low', 'Moderate', 'High', 'Critical']) {
      expect(bars.filter((b) => b === band)).toHaveLength(5);
    }
    expect(chart.querySelector('line[x1="72"][x2="72"]')).not.toBeNull();
  });

  it('shows a placeholder, and no failure, while the first page loads', async () => {
    const first = gate(isPage);
    showExplorer('/explorer', {}, [first.handler]);
    await loaded();
    expect(await within(customers()).findByText('Loading scored customers…')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    first.release();
    expect(rowButtons(await table())).toHaveLength(15);
  });

  it('says why, and shows no figure, when the book cannot be loaded', async () => {
    const refused = gate(isPage, { fail: true });
    showExplorer('/explorer', {}, [refused.handler]);
    expect(await within(customers()).findByText('Loading scored customers…')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    refused.release();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(
      'The customer book could not be loaded. Every figure on this page comes from the API, so none is shown until it answers.',
    );
    expect(alert).not.toHaveTextContent('Refused by a test.');
    server.use(...explorerApi());
    // The button goes as the list loads again: the reader continues at the list.
    await press(within(alert).getByRole('button', { name: 'Try again' }));
    expect(customers()).toHaveFocus();
    expect(rowButtons(await table())).toHaveLength(15);
  });

  it('shows a dash, not a wait, for figures that could not be loaded', async () => {
    showExplorer('/explorer', {}, [failing((url) => !isPage(url))]);
    await table();
    await waitFor(() =>
      expect(within(summaryTile('Customers')).getByText('—')).toBeInTheDocument(),
    );
    expect(within(summaryTile('Mean probability')).getByText('—')).toBeVisible();
    expect(
      screen.getByText(
        "All rows with their served probability. Outcomes appear for train and validation customers only; the test partition's are withheld.",
      ),
    ).toBeVisible();
    expect(screen.getByRole('link', { name: 'Export 37 rows' })).toBeVisible();

    const summary = screen.getByRole('region', { name: 'Selection summary' });
    const alert = within(summary).getByRole('alert');
    expect(alert).toHaveTextContent('The selection’s figures could not be loaded.');
    server.use(...explorerApi());
    await press(within(alert).getByRole('button', { name: 'Try again' }));
    expect(summary).toHaveFocus();
    await waitFor(() =>
      expect(within(summaryTile('Customers')).getByText('37')).toBeInTheDocument(),
    );
    expect(within(summary).queryByRole('alert')).not.toBeInTheDocument();
  });

  it('says every outcome is withheld when the provenance is not verified', async () => {
    showExplorer('/explorer', { withheld: 'Retrain with make train.' });
    server.use(
      answer(
        'get',
        '/api/model-info',
        modelInfo({
          provenance: {
            verified: false,
            data_sha256: null,
            split_digest: null,
            withheld_reason: 'Retrain with make train.',
          },
        }),
      ),
    );
    await table();
    expect(
      await screen.findByText(
        'All 37 rows with their served probability. Observed outcomes are withheld: Retrain with make train.',
      ),
    ).toBeInTheDocument();
    expect(within(summaryTile('Observed churn')).getByText('Withheld')).toBeInTheDocument();
  });
});

describe('Explorer: saved views', () => {
  it('count each view’s customers, and apply one over a clean set of filters', async () => {
    showExplorer('/explorer?q=DEMO-000&page=2');
    await table();
    const views = screen.getByRole('group', { name: 'Saved views' });
    const chip = (name: RegExp) => within(views).getByRole('button', { name });
    await waitFor(() => {
      expect(chip(/^All customers/)).toHaveAccessibleName('All customers 37');
      expect(chip(/^Flagged · month-to-month/)).toHaveAccessibleName('Flagged · month-to-month 8');
      expect(chip(/^First six months/)).toHaveAccessibleName('First six months 4');
      expect(chip(/^Fiber \+ e-check/)).toHaveAccessibleName('Fiber + e-check 3');
      expect(chip(/^Two-year contracts/)).toHaveAccessibleName('Two-year contracts 12');
    });
    expect(chip(/^All customers/)).toHaveAttribute('aria-pressed', 'false');

    const added = entriesAdded();
    await userEvent.click(chip(/^Fiber \+ e-check/));
    expect(window.location.search).toBe('?internet=Fiber+optic&payment=Electronic+check');
    expect(added()).toBe(1);
    expect(chip(/^Fiber \+ e-check/)).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('searchbox', { name: 'Customer ID' })).toHaveValue('');
    await waitFor(() =>
      expect(rowButtons(screen.getByRole('table', { name: 'Customers' }))).toHaveLength(3),
    );
  });
});

describe('Explorer: what is typed, then a view', () => {
  it('writes the search first, so the view applies over it and Back returns to it', async () => {
    showExplorer();
    await table();
    const views = screen.getByRole('group', { name: 'Saved views' });
    const box = screen.getByRole('searchbox', { name: 'Customer ID' });
    await userEvent.type(box, 'DEMO-00');
    await userEvent.click(within(views).getByRole('button', { name: /^Two-year contracts/ }));
    expect(window.location.search).toBe('?contract=Two+year');
    expect(box).toHaveValue('');
    expect(within(views).getByRole('button', { name: /^Two-year contracts/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await act(still);
    expect(window.location.search).toBe('?contract=Two+year');

    act(() => window.history.back());
    await waitFor(() => expect(window.location.search).toBe('?q=DEMO-00'));
    await waitFor(() => expect(box).toHaveValue('DEMO-00'));
  });
});

describe('Explorer: filters', () => {
  it('asks for nothing with no band chosen, and says so', async () => {
    const seen = recordRequests();
    showExplorer('/explorer?bands=');
    await loaded();
    expect(await screen.findByText('No customer matches these filters')).toBeInTheDocument();
    expect(within(customers()).getByText('No band is selected.')).toBeVisible();
    // Said once, by the list: the summary's status stays quiet.
    expect(selectionStatus()).toBeEmptyDOMElement();
    const bands = screen.getByRole('group', { name: 'Band' });
    for (const chip of within(bands).getAllByRole('button')) {
      expect(chip).toHaveAttribute('aria-pressed', 'false');
    }
    expect(screen.getByRole('button', { name: 'Export 0 rows' })).toBeDisabled();
    expect(within(summaryTile('Customers')).getByText('0')).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(listings(seen)).toEqual([]);

    const added = entriesAdded();
    await userEvent.click(within(bands).getByRole('button', { name: 'High' }));
    expect(search().getAll('bands')).toEqual(['High']);
    expect(added()).toBe(1);
    await waitFor(() => expect(listings(seen)[0]?.searchParams.getAll('bands')).toEqual(['High']));
  });

  it('leaves a band out when its chip is pressed off, from the first page', async () => {
    showExplorer('/explorer?page=2');
    await table();
    await userEvent.click(
      within(screen.getByRole('group', { name: 'Band' })).getByRole('button', { name: 'Low' }),
    );
    expect(search().getAll('bands')).toEqual(['Moderate', 'High', 'Critical']);
    expect(search().get('page')).toBeNull();
  });

  it('settles a tenure bound before asking, and refuses a range that holds nobody', async () => {
    const seen = recordRequests();
    showExplorer();
    await table();
    const added = entriesAdded();
    const max = screen.getByRole('textbox', { name: 'Maximum tenure' });
    await userEvent.type(max, '06');
    expect(max).toHaveValue('6');
    await waitFor(() => expect(search().get('tenure_max')).toBe('6'));
    expect(added()).toBe(0);
    await waitFor(() => expect(listings(seen).at(-1)?.searchParams.get('tenure_max')).toBe('6'));

    const min = screen.getByRole('textbox', { name: 'Minimum tenure' });
    await userEvent.type(min, '9x');
    expect(min).toHaveValue('9');
    await waitFor(() => expect(search().get('tenure_min')).toBe('9'));
    expect(added()).toBe(0);
    expect(min).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Minimum is above maximum.')).toBeInTheDocument();
    expect(
      await within(screen.getByRole('region', { name: 'Customers' })).findByText(
        'The minimum tenure is above the maximum.',
      ),
    ).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(listings(seen).some((url) => url.searchParams.get('tenure_min') === '9')).toBe(false);
    // Nothing is loading: the last rows are not standing in for anything.
    expect(customers()).toHaveAttribute('aria-busy', 'false');
  });

  it('keeps both bounds typed one after the other, under the range’s caption', async () => {
    showExplorer();
    await table();
    const range = screen.getByRole('group', { name: 'Tenure, months' });
    await userEvent.type(within(range).getByRole('textbox', { name: 'Minimum tenure' }), '3');
    await userEvent.type(within(range).getByRole('textbox', { name: 'Maximum tenure' }), '9');
    await waitFor(() => expect(search().get('tenure_max')).toBe('9'));
    expect(search().get('tenure_min')).toBe('3');
  });

  it('flags a range that holds nobody even while no band is chosen', async () => {
    showExplorer('/explorer?bands=&tenure_min=9&tenure_max=3');
    await loaded();
    expect(await screen.findByText('Minimum is above maximum.')).toBeVisible();
    expect(screen.getByRole('textbox', { name: 'Minimum tenure' })).toHaveAttribute(
      'aria-invalid',
      'true',
    );
    expect(within(customers()).getByText('No band is selected.')).toBeVisible();
  });

  it('never asks for a range from the address that holds nobody', async () => {
    const seen = recordRequests();
    showExplorer('/explorer?tenure_min=9&tenure_max=3');
    await loaded();
    expect(
      await within(screen.getByRole('region', { name: 'Customers' })).findByText(
        'The minimum tenure is above the maximum.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Minimum tenure' })).toHaveValue('9');
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(listings(seen)).toEqual([]);
  });

  it('searches by ID once typing settles, keeping only what an ID can hold', async () => {
    const seen = recordRequests();
    showExplorer();
    await table();
    const added = entriesAdded();
    const box = screen.getByRole('searchbox', { name: 'Customer ID' });
    await userEvent.type(box, 'DEMO-003 ');
    expect(box).toHaveValue('DEMO-003');
    await waitFor(() => expect(search().get('q')).toBe('DEMO-003'));
    expect(added()).toBe(0);
    await waitFor(() => expect(screen.getByText(/^Showing 1–8 of 8/)).toBeInTheDocument());
    expect(listings(seen).at(-1)?.searchParams.get('q')).toBe('DEMO-003');
  });

  it('changes nothing when what was typed is erased before it settles', async () => {
    showExplorer('/explorer?page=2');
    await table();
    const box = screen.getByRole('searchbox', { name: 'Customer ID' });
    await userEvent.type(box, 'x{Backspace}');
    await act(still);
    expect(window.location.search).toBe('?page=2');
  });

  it('filters by contract, internet and partition, counts them, and resets', async () => {
    showExplorer();
    await table();
    const reset = screen.getByRole('button', { name: 'Reset' });
    expect(reset).toHaveAttribute('aria-disabled', 'true');
    const added = entriesAdded();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Contract' }), 'Two year');
    expect(search().get('contract')).toBe('Two year');
    expect(added()).toBe(1);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Internet' }), 'None');
    expect(search().get('internet')).toBe('No');
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Partition' }),
      'Validation',
    );
    expect(search().get('part')).toBe('validation');
    const counted = screen.getByRole('button', { name: 'Reset (3)' });
    await userEvent.click(counted);
    expect(window.location.search).toBe('');
    // Nothing left to reset, and the focus stays where it was.
    expect(counted).toHaveAccessibleName('Reset');
    expect(counted).toHaveAttribute('aria-disabled', 'true');
    expect(counted).toHaveFocus();
    await userEvent.click(counted);
    expect(window.location.search).toBe('');
  });

  it('filters by payment from the first page, and lets it go with “Any”', async () => {
    showExplorer('/explorer?page=2');
    await table();
    const payment = screen.getByRole('combobox', { name: 'Payment' });
    const added = entriesAdded();
    await userEvent.selectOptions(payment, 'Electronic check');
    expect(search().get('payment')).toBe('Electronic check');
    expect(search().get('page')).toBeNull();
    expect(added()).toBe(1);
    await userEvent.selectOptions(payment, 'Any');
    expect(search().get('payment')).toBeNull();
  });

  it('names the test partition by what it withholds', async () => {
    showExplorer();
    await table();
    expect(
      within(screen.getByRole('combobox', { name: 'Partition' })).getByRole('option', {
        name: 'Test (outcome withheld)',
      }),
    ).toHaveValue('test');
  });

  it('does nothing from a reset with nothing to reset, the page included', async () => {
    showExplorer('/explorer?page=2');
    await table();
    await userEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(window.location.search).toBe('?page=2');
  });

  it('flags or unflags by the served decision', async () => {
    showExplorer();
    await table();
    const flag = screen.getByRole('group', { name: 'Decision at 0.36' });
    await userEvent.click(within(flag).getByRole('button', { name: 'Not flagged' }));
    expect(search().get('flagged')).toBe('false');
    await waitFor(() => expect(screen.getByText(/^Showing 1–13 of 13/)).toBeInTheDocument());
    await userEvent.click(within(flag).getByRole('button', { name: 'All' }));
    expect(search().get('flagged')).toBeNull();
  });
});

describe('Explorer: the table', () => {
  it('sorts by a column from its own direction, and says so', async () => {
    showExplorer('/explorer?page=2');
    const found = await table();
    await userEvent.click(within(found).getByRole('button', { name: 'Customer' }));
    expect(search().get('sort')).toBe('id');
    expect(search().get('dir')).toBe('asc');
    expect(search().get('page')).toBeNull();
    const header = within(found).getByRole('columnheader', { name: 'Customer' });
    expect(header).toHaveAttribute('aria-sort', 'ascending');
    await userEvent.click(within(found).getByRole('button', { name: 'Customer' }));
    expect(search().get('dir')).toBeNull();
    expect(header).toHaveAttribute('aria-sort', 'descending');
  });

  it('pages, and keeps the reader’s place when the page size changes', async () => {
    showExplorer();
    await table();
    const added = entriesAdded();
    await userEvent.click(screen.getByRole('button', { name: 'Next page' }));
    expect(search().get('page')).toBe('2');
    expect(added()).toBe(1);
    expect(await screen.findByText(/^Showing 16–30 of 37/)).toBeInTheDocument();

    await userEvent.click(await screen.findByRole('button', { name: /^DEMO-0026 / }));
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Rows per page' }), '25');
    expect(search().get('size')).toBe('25');
    expect(search().get('page')).toBe('2');
    expect(added()).toBe(3);
    expect(await screen.findByText(/^Showing 26–37 of 37/)).toBeInTheDocument();
  });

  it('keeps the first row’s place with nobody open', async () => {
    showExplorer('/explorer?page=3');
    await screen.findByText(/^Showing 31–37 of 37/);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Rows per page' }), '25');
    expect(search().get('page')).toBe('2');
    expect(await screen.findByText(/^Showing 26–37 of 37/)).toBeInTheDocument();
  });

  it('pages from the address while the last view’s rows stand in', async () => {
    const pending = gate((url) => isPage(url) && url.searchParams.get('contract') === 'One year');
    showExplorer('/explorer?page=3', {}, [pending.handler]);
    await screen.findByText(/^Showing 31–37 of 37/);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Contract' }), 'One year');
    await waitFor(() => expect(customers()).toHaveAttribute('aria-busy', 'true'));
    expect(screen.getByText('1 / 3')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Rows per page' }), '25');
    expect(search().get('page')).toBeNull();
    pending.release();
    expect(await screen.findByText(/^Showing 1–12 of 12/)).toBeInTheDocument();
  });

  it('keeps the selection’s figures while only the page, the order or the size changes', async () => {
    const pending = gate((url) => isPage(url) && url.searchParams.get('page') === '2');
    showExplorer('/explorer', {}, [pending.handler]);
    await table();
    expect(await screen.findByText('37 customers match, 24 flagged.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Next page' }));
    await waitFor(() => expect(customers()).toHaveAttribute('aria-busy', 'true'));
    expect(within(summaryTile('Customers')).getByText('37')).toBeVisible();
    expect(selectionStatus()).toHaveTextContent('37 customers match, 24 flagged.');
    expect(screen.getByRole('link', { name: 'Export 37 rows' })).toBeVisible();
    pending.release();
    expect(await screen.findByText(/^Showing 16–30 of 37/)).toBeInTheDocument();
  });

  it('never shows the last view’s figures while the new ones load', async () => {
    const pending = gate((url) => url.searchParams.get('contract') === 'One year');
    showExplorer('/explorer', {}, [pending.handler]);
    await table();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Contract' }), 'One year');
    const section = customers();
    await waitFor(() => expect(section).toHaveAttribute('aria-busy', 'true'));
    expect(within(summaryTile('Customers')).getByText('…')).toBeInTheDocument();
    expect(screen.queryByText('37 customers match, 24 flagged.')).not.toBeInTheDocument();
    pending.release();
    await waitFor(() => expect(section).toHaveAttribute('aria-busy', 'false'));
    await waitFor(() =>
      expect(within(summaryTile('Customers')).getByText('12')).toBeInTheDocument(),
    );
  });

  it('explains an empty result with the served bands’ own figures', async () => {
    showExplorer('/explorer?bands=Low&flagged=true');
    expect(
      await screen.findByText(
        'Low tops out at 0.25, below the 0.36 threshold, so nobody in it is flagged.',
      ),
    ).toBeInTheDocument();
  });

  it('asks to loosen the filters otherwise, and resets them', async () => {
    showExplorer('/explorer?q=ZZZ');
    expect(await screen.findByText('Loosen a filter or clear the ID search.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Reset filters' }));
    expect(window.location.search).toBe('');
    // The button is gone with the empty state: the focus goes to the list.
    expect(customers()).toHaveFocus();
    expect(rowButtons(await table())).toHaveLength(15);
  });
});

describe('Explorer: one customer', () => {
  it('opens a customer’s profile beside the list, and returns focus when it closes', async () => {
    showExplorer();
    const found = await table();
    const row = within(found).getByRole('button', { name: /^DEMO-0002 / });
    expect(row).toHaveAttribute('aria-expanded', 'false');
    expect(row).not.toHaveAttribute('aria-controls');
    const added = entriesAdded();
    await userEvent.click(row);
    expect(search().get('id')).toBe('DEMO-0002');
    expect(added()).toBe(1);
    expect(row).toHaveAttribute('aria-expanded', 'true');
    expect(row).toHaveAttribute('aria-controls', 'customer-detail');
    const detail = screen.getByRole('complementary', { name: 'Customer detail' });
    expect(detail).toHaveAttribute('id', 'customer-detail');
    expect(within(detail).getByRole('heading', { name: 'DEMO-0002' })).toHaveFocus();
    expect(await within(detail).findByText('Total charges')).toBeInTheDocument();
    expect(within(detail).getByText('Flagged at 0.36')).toBeInTheDocument();
    expect(screen.getByText('Showing 1–15 of 37')).toBeInTheDocument();

    await userEvent.click(within(detail).getByRole('button', { name: 'Close detail' }));
    expect(search().get('id')).toBeNull();
    expect(
      screen.queryByRole('complementary', { name: 'Customer detail' }),
    ).not.toBeInTheDocument();
    expect(within(found).getByRole('button', { name: /^DEMO-0002 / })).toHaveFocus();
  });

  it('closes the open customer from its own row', async () => {
    showExplorer('/explorer?id=DEMO-0002');
    const found = await table();
    const row = within(found).getByRole('button', { name: /^DEMO-0002 / });
    await userEvent.click(row);
    expect(search().get('id')).toBeNull();
    expect(
      screen.queryByRole('complementary', { name: 'Customer detail' }),
    ).not.toBeInTheDocument();
    expect(row).toHaveFocus();
  });

  it('returns focus to the list when the closed customer is not on the page', async () => {
    showExplorer('/explorer?id=DEMO-0020');
    await table();
    await userEvent.click(within(detail()).getByRole('button', { name: 'Close detail' }));
    expect(customers()).toHaveFocus();
  });

  it('gives an unflagged customer’s inputs as the profile shows them', async () => {
    showExplorer('/explorer?page=2&id=DEMO-0030');
    await table();
    const panel = detail();
    expect(await within(panel).findByText('Not flagged')).toBeVisible();
    expect(within(panel).getByText('21.1%')).toBeVisible();
    expect(within(panel).getByText('57 mo')).toBeVisible();
    expect(within(panel).getByText('Two year')).toBeVisible();
    expect(within(panel).getByText('$37.00')).toBeVisible();
    expect(within(panel).getByText('$2,109.00')).toBeVisible();
    expect(within(panel).getByText('Mailed check')).toBeVisible();
  });

  it('says when a customer could not be loaded, with a retry', async () => {
    showExplorer('/explorer?id=DEMO-0002', {}, [
      refuse('get', '/api/customers/{customer_id}', 500, 'internal_error'),
    ]);
    await table();
    const alert = await within(detail()).findByRole('alert');
    expect(alert).toHaveTextContent('This customer could not be loaded.');
    expect(within(detail()).queryByText(/There is no customer/)).not.toBeInTheDocument();
    server.use(...explorerApi());
    await press(within(alert).getByRole('button', { name: 'Try again' }));
    expect(within(detail()).getByRole('heading', { level: 2, name: 'DEMO-0002' })).toHaveFocus();
    expect(await within(detail()).findByText('Total charges')).toBeVisible();
  });

  it('steps to the next and previous customer within the page', async () => {
    const seen = recordRequests();
    showExplorer('/explorer?id=DEMO-0002');
    await table();
    const detail = screen.getByRole('complementary', { name: 'Customer detail' });
    const count = listings(seen).length;
    await userEvent.click(
      within(detail).getByRole('button', { name: 'Next customer in the list' }),
    );
    expect(search().get('id')).toBe('DEMO-0003');
    await userEvent.click(
      within(detail).getByRole('button', { name: 'Previous customer in the list' }),
    );
    await userEvent.click(
      within(detail).getByRole('button', { name: 'Previous customer in the list' }),
    );
    expect(search().get('id')).toBe('DEMO-0001');
    expect(listings(seen)).toHaveLength(count);
  });

  it('crosses to the next page with one request, and wraps at both ends', async () => {
    const seen = recordRequests();
    showExplorer('/explorer?id=DEMO-0015');
    await table();

    await userEvent.click(next());
    await waitFor(() => expect(search().get('id')).toBe('DEMO-0016'));
    expect(search().get('page')).toBe('2');
    expect(listings(seen).filter((url) => url.searchParams.get('page') === '2')).toHaveLength(1);
    expect(await screen.findByText(/^Showing 16–30 of 37/)).toBeInTheDocument();

    act(() => {
      window.history.replaceState(null, '', '/explorer?page=3&id=DEMO-0037');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await screen.findByText(/^Showing 31–37 of 37/);
    await userEvent.click(next());
    await waitFor(() => expect(search().get('id')).toBe('DEMO-0001'));
    expect(search().get('page')).toBeNull();
    await screen.findByText(/^Showing 1–15 of 37/);
    await userEvent.click(previous());
    await waitFor(() => expect(search().get('id')).toBe('DEMO-0037'));
    expect(search().get('page')).toBe('3');
  });

  it('crosses a page as one history entry, keeping focus and the controls usable', async () => {
    const pending = gate((url) => isPage(url) && url.searchParams.get('page') === '2');
    showExplorer('/explorer?id=DEMO-0015', {}, [pending.handler]);
    await table();
    const added = entriesAdded();
    await userEvent.click(next());
    await waitFor(() => expect(detail()).toHaveAttribute('aria-busy', 'true'));
    expect(next()).toBeEnabled();
    expect(next()).toHaveAttribute('aria-disabled', 'false');
    expect(next()).toHaveFocus();
    pending.release();
    await waitFor(() => expect(search().get('id')).toBe('DEMO-0016'));
    expect(detail()).toHaveAttribute('aria-busy', 'false');
    expect(next()).toHaveFocus();
    expect(added()).toBe(1);

    act(() => window.history.back());
    await waitFor(() => expect(search().get('id')).toBe('DEMO-0015'));
    expect(search().get('page')).toBeNull();
  });

  it('drops a step the reader has moved on from, even back to the same address', async () => {
    const pending = gate((url) => isPage(url) && url.searchParams.get('page') === '2');
    showExplorer('/explorer?id=DEMO-0015', {}, [pending.handler]);
    await table();
    await userEvent.click(next());
    await waitFor(() => expect(detail()).toHaveAttribute('aria-busy', 'true'));
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Contract' }), 'One year');
    // Moved on: the panel is no longer waiting for that step.
    expect(detail()).toHaveAttribute('aria-busy', 'false');
    act(() => window.history.back());
    await waitFor(() => expect(window.location.search).toBe('?id=DEMO-0015'));
    pending.release();
    await waitFor(() => expect(detail()).toHaveAttribute('aria-busy', 'false'));
    await act(still);
    expect(window.location.search).toBe('?id=DEMO-0015');
  });

  it('lands no step on a search typed while its page loads', async () => {
    const pending = gate((url) => isPage(url) && url.searchParams.get('page') === '2');
    showExplorer('/explorer?id=DEMO-0015', {}, [pending.handler]);
    await table();
    await userEvent.click(next());
    await userEvent.type(screen.getByRole('searchbox', { name: 'Customer ID' }), 'DEMO-002');
    pending.release();
    await waitFor(() => expect(detail()).toHaveAttribute('aria-busy', 'false'));
    expect(search().get('q')).toBe('DEMO-002');
    expect(search().get('id')).toBe('DEMO-0015');
    expect(search().get('page')).toBeNull();
  });

  it('takes a search typed just before a step as the reader’s choice, not the step', async () => {
    showExplorer('/explorer?id=DEMO-0002');
    await table();
    await userEvent.type(screen.getByRole('searchbox', { name: 'Customer ID' }), 'DEMO-002');
    await userEvent.click(next());
    expect(search().get('q')).toBe('DEMO-002');
    expect(search().get('id')).toBe('DEMO-0002');
  });

  it('stays put when the next page cannot be loaded, and steps once it can', async () => {
    showExplorer('/explorer?id=DEMO-0015', {}, [
      failing((url) => isPage(url) && url.searchParams.get('page') === '2'),
    ]);
    await table();
    await userEvent.click(next());
    expect(
      await within(detail()).findByText('The next customer could not be loaded.'),
    ).toBeVisible();
    expect(window.location.search).toBe('?id=DEMO-0015');
    expect(screen.getByText('Showing 1–15 of 37')).toBeInTheDocument();
    expect(next()).toHaveFocus();
    expect(next()).toHaveAttribute('aria-disabled', 'false');

    server.use(...explorerApi());
    await userEvent.click(next());
    await waitFor(() => expect(search().get('id')).toBe('DEMO-0016'));
    expect(within(detail()).queryByText(/could not be loaded/)).not.toBeInTheDocument();
  });

  it('says which way a failed step went, and forgets it once the reader moves on', async () => {
    showExplorer('/explorer?page=2&id=DEMO-0016', {}, [
      failing((url) => isPage(url) && url.searchParams.get('page') === '1'),
    ]);
    await table();
    await userEvent.click(previous());
    expect(
      await within(detail()).findByText('The previous customer could not be loaded.'),
    ).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: /^DEMO-0020 / }));
    expect(search().get('id')).toBe('DEMO-0020');
    expect(within(detail()).queryByText(/could not be loaded/)).not.toBeInTheDocument();
  });

  it('keeps a customer the list no longer shows, without stepping from it', async () => {
    showExplorer('/explorer?id=DEMO-0020');
    await table();
    const seen = recordRequests();
    expect(await within(detail()).findByText('Total charges')).toBeInTheDocument();
    expect(next()).toHaveAttribute('aria-disabled', 'true');
    expect(previous()).toHaveAttribute('aria-disabled', 'true');
    expect(
      within(detail()).getByText(
        'Previous and next walk the rows shown; this customer is not among them.',
      ),
    ).toBeInTheDocument();
    await userEvent.click(next());
    await userEvent.click(previous());
    expect(search().get('id')).toBe('DEMO-0020');
    expect(listings(seen)).toEqual([]);
  });

  it('says when there is no such customer', async () => {
    showExplorer('/explorer?id=NOPE-1');
    await table();
    expect(await screen.findByText('There is no customer NOPE-1 in the book.')).toBeInTheDocument();
  });

  it('explains a customer on request, strongest seven first, in the server’s own terms', async () => {
    const seen = recordRequests();
    const bodies: unknown[] = [];
    server.events.on('request:start', ({ request }) => {
      if (request.method === 'POST')
        void request
          .clone()
          .json()
          .then((body) => bodies.push(body));
    });
    showExplorer('/explorer?id=DEMO-0001');
    await table();
    const detail = screen.getByRole('complementary', { name: 'Customer detail' });
    expect(seen.some((url) => url.pathname === '/api/explain')).toBe(false);
    await userEvent.click(within(detail).getByRole('button', { name: 'Explain this customer' }));
    const list = await within(detail).findByRole('list', { name: 'Attributions' });
    const items = within(list).getAllByRole('listitem');
    expect(items).toHaveLength(7);
    expect(items[0]).toHaveTextContent('Tenure+31.7');
    expect(items[1]).toHaveTextContent('Monthly charges−20.9');
    expect(within(list).queryByText('Paperless billing')).not.toBeInTheDocument();
    expect(within(detail).getByText('base 26.5% → 97.4%')).toBeInTheDocument();
    expect(
      within(detail).getByText(
        "Percentage points against the model's average over 100 training customers; orange raises, blue lowers. Computed on the server with 10 feature orderings. Model attributions, not causal effects.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(bodies).toEqual([{ customer_ids: ['DEMO-0001'] }]));
  });

  it('says when attributions are unavailable, and asks again on request', async () => {
    const seen = recordRequests();
    showExplorer('/explorer?id=DEMO-0001');
    server.use(refuse('post', '/api/explain', 503, 'explanations_busy'));
    await table();
    const panel = detail();
    const moved = () => within(panel).getByRole('heading', { name: 'What moved this score' });
    // The button goes while they are computed: the reader continues at their heading.
    await press(within(panel).getByRole('button', { name: 'Explain this customer' }));
    expect(moved()).toHaveFocus();
    expect(
      await within(panel).findByText(
        'Attributions are unavailable right now.',
        {},
        { timeout: 3000 },
      ),
    ).toBeInTheDocument();
    // A busy server is asked once more by itself before the page says so.
    expect(seen.filter((url) => url.pathname === '/api/explain')).toHaveLength(2);
    const again = within(panel).getByRole('button', { name: 'Explain this customer' });
    expect(again).toBeEnabled();

    server.use(...explorerApi());
    await press(again);
    expect(moved()).toHaveFocus();
    expect(await within(panel).findByRole('list', { name: 'Attributions' })).toBeVisible();
    expect(seen.filter((url) => url.pathname === '/api/explain')).toHaveLength(3);
  });

  it('explains only the customer it was asked for', async () => {
    const seen = recordRequests();
    showExplorer('/explorer?id=DEMO-0001');
    await table();
    await userEvent.click(within(detail()).getByRole('button', { name: 'Explain this customer' }));
    await within(detail()).findByRole('list', { name: 'Attributions' });
    await userEvent.click(next());
    await waitFor(() => expect(search().get('id')).toBe('DEMO-0002'));
    expect(await within(detail()).findByText('Total charges')).toBeVisible();
    expect(within(detail()).getByRole('button', { name: 'Explain this customer' })).toBeVisible();
    await act(still);
    expect(seen.filter((url) => url.pathname === '/api/explain')).toHaveLength(1);
  });
});

describe('Explorer: export', () => {
  it('downloads the customers the filters keep, in the order shown', async () => {
    showExplorer('/explorer?contract=One+year&sort=tenure&dir=asc');
    await table();
    const link = await screen.findByRole('link', { name: 'Export 12 rows' });
    expect(link).toHaveAttribute(
      'href',
      '/api/customers/export?contract=One+year&sort=tenure&dir=asc',
    );
    expect(link).toHaveAttribute('download');
  });
});
