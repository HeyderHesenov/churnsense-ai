import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { navigate } from '../../app/router';
import { recordRequests, server } from '../../test/server';
import { renderV2 } from '../test/render';
import {
  ACCOUNT,
  accountDetail,
  accountPage,
  ACCOUNTS,
  SUMMARY,
  summary,
  v2Answer,
  v2Refuse,
  v2Respond,
} from '../test/v2Api';

/** The list's query strings, in the order the page asked. */
function listQueries(): URLSearchParams[] {
  const seen: URLSearchParams[] = [];
  server.events.on('request:start', ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname === ACCOUNTS) seen.push(url.searchParams);
  });
  return seen;
}

describe('the portfolio', () => {
  it('shows the synthetic dataset, its snapshot date and the API’s figures', async () => {
    server.use(v2Answer('get', SUMMARY, summary()));
    renderV2('/v2/portfolio');

    const banner = await screen.findByRole('note', { name: 'About this data' });
    expect(banner).toHaveTextContent('Synthetic demo data');
    expect(banner).toHaveTextContent('Snapshot 31 October 2026');
    expect(banner).toHaveTextContent('500 generated accounts');
    expect(banner).toHaveTextContent('Not your uploaded file, not real customers');

    const tiles = screen.getByRole('group', { name: 'Accounts by rule level' });
    expect(within(tiles).getByText('Attention').closest('a')).toHaveAttribute(
      'href',
      '/v2/accounts?level=attention',
    );
    for (const figure of ['500', '184', '79', '132', '105']) {
      expect(within(tiles).getByText(figure)).toBeInTheDocument();
    }
    expect(within(tiles).getByText('22 trialing · 29 paused · 54 canceled')).toBeInTheDocument();

    const usd = screen.getByRole('row', { name: /^USD/ });
    expect(usd).toHaveTextContent('$2,180,361.10');
    expect(usd).toHaveTextContent('$1,237,413.44');
    const eur = screen.getByRole('row', { name: /^EUR/ });
    expect(eur).toHaveTextContent('€364,090.44');
    expect(screen.getByText(/not a forecast of lost revenue/)).toBeInTheDocument();

    expect(screen.getByText('0–30 days · attention').nextElementSibling).toHaveTextContent(
      '50 accounts',
    );
    expect(screen.getByText(/not by a model: there is no probability/)).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Rules' })).toHaveTextContent('payment_past_due');
    expect(screen.getByText(/Synthetic preview data: every account/)).toBeInTheDocument();
  });

  it('says so when the summary cannot be loaded, and loads it again', async () => {
    let calls = 0;
    server.use(
      http.get(SUMMARY, () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json(
              { error: 'internal_error', detail: 'x', problems: [] },
              { status: 500 },
            )
          : HttpResponse.json(summary());
      }),
    );
    renderV2('/v2/portfolio');
    expect(await screen.findByText(/summary could not be loaded/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Try again/ }));
    expect(await screen.findByRole('note', { name: 'About this data' })).toBeInTheDocument();
  });

  it('shows a placeholder while the summary loads', async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      v2Respond('get', SUMMARY, async () => {
        await held;
        return summary();
      }),
    );
    renderV2('/v2/portfolio');
    expect(await screen.findByText('Loading the portfolio')).toBeInTheDocument();
    release();
    expect(await screen.findByRole('note', { name: 'About this data' })).toBeInTheDocument();
  });
});

describe('the account list', () => {
  beforeEach(() => server.use(v2Answer('get', SUMMARY, summary())));

  it('shows the API’s rows: level, signals, amount, renewal and where to look', async () => {
    const queries = listQueries();
    server.use(v2Answer('get', ACCOUNTS, accountPage()));
    renderV2('/v2/accounts');

    const row = await screen.findByRole('row', { name: /Synthetic Copper Works #188/ });
    // The API's own page, pages and total.
    expect(screen.getByText('Page 1 of 20 · 500 accounts')).toBeInTheDocument();
    expect(within(row).getByText('Attention')).toBeInTheDocument();
    expect(within(row).getByText('Renewal approaching')).toBeInTheDocument();
    expect(within(row).getByText('€2,890.38')).toBeInTheDocument();
    expect(within(row).getByText('31 Oct 2026')).toBeInTheDocument();
    expect(within(row).getByText('in 0 days')).toBeInTheDocument();
    expect(within(row).getByText(/Prepare for the renewal/)).toBeInTheDocument();
    expect(within(row).getByRole('link')).toHaveAttribute('href', '/v2/account?id=DEMO-ACC-000188');

    const paused = screen.getByRole('row', { name: /Synthetic Pine Labs #7/ });
    expect(within(paused).getAllByText('Not assessed')).toHaveLength(2);

    expect(screen.getByRole('note', { name: 'About this data' })).toHaveTextContent(
      '31 October 2026',
    );
    expect(queries[0]?.toString()).toBe('sort=priority&page=1&page_size=25');
  });

  it('asks the API for each filter, sort and page the reader chooses', async () => {
    const queries = listQueries();
    server.use(v2Respond('get', ACCOUNTS, () => accountPage()));
    renderV2('/v2/accounts');
    await screen.findByRole('row', { name: /Synthetic Copper Works/ });

    await userEvent.click(screen.getByRole('button', { name: 'Attention' }));
    await waitFor(() => expect(queries.at(-1)?.getAll('level')).toEqual(['attention']));
    await userEvent.click(screen.getByRole('button', { name: 'Watch' }));
    await waitFor(() => expect(queries.at(-1)?.getAll('level')).toEqual(['attention', 'watch']));
    await userEvent.click(screen.getByRole('button', { name: 'Past due' }));
    await waitFor(() => expect(queries.at(-1)?.getAll('status')).toEqual(['past_due']));

    const order = screen.getByRole('combobox', { name: 'Order' });
    const byMrr = within(order).getByRole('option', { name: /Highest MRR/ });
    expect(byMrr).toBeDisabled();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Currency' }), 'USD');
    await waitFor(() => expect(queries.at(-1)?.get('currency')).toBe('USD'));
    expect(byMrr).toBeEnabled();
    await userEvent.selectOptions(order, 'mrr_desc');
    await waitFor(() => expect(queries.at(-1)?.get('sort')).toBe('mrr_desc'));
    // A currency cleared takes the one-currency order with it.
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Currency' }),
      'All currencies',
    );
    // That view was loaded before, so it is shown from the cache rather than asked again.
    await waitFor(() => expect(order).toHaveValue('priority'));
    expect(window.location.search).not.toMatch(/currency|sort/);

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Renewing within' }), '90');
    await waitFor(() => expect(queries.at(-1)?.get('renewal_within_days')).toBe('90'));

    await userEvent.type(screen.getByRole('searchbox', { name: /Search account/ }), 'Copper');
    await waitFor(() => expect(queries.at(-1)?.get('q')).toBe('Copper'));
    await userEvent.type(screen.getByRole('textbox', { name: 'Segment (exact name)' }), 'SMB');
    await waitFor(() => expect(queries.at(-1)?.get('segment')).toBe('SMB'));

    await userEvent.click(screen.getByRole('button', { name: /Next page/ }));
    await waitFor(() => expect(queries.at(-1)?.get('page')).toBe('2'));
    expect(window.location.search).toContain('page=2');
  });

  it('reads a linked view from the address', async () => {
    const queries = listQueries();
    server.use(v2Answer('get', ACCOUNTS, accountPage()));
    renderV2('/v2/accounts?level=watch&status=active&currency=EUR&sort=mrr_desc&page_size=50');
    await screen.findByRole('row', { name: /Synthetic Copper Works/ });
    const query = queries[0];
    expect(query?.getAll('level')).toEqual(['watch']);
    expect(query?.getAll('status')).toEqual(['active']);
    expect(query?.get('currency')).toBe('EUR');
    expect(query?.get('sort')).toBe('mrr_desc');
    expect(query?.get('page_size')).toBe('50');
    expect(screen.getByRole('button', { name: 'Watch' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('an empty result offers to clear the filters', async () => {
    server.use(
      v2Respond('get', ACCOUNTS, (request) =>
        new URL(request.url).searchParams.has('q')
          ? accountPage({ total: 0, pages: 0, rows: [] })
          : accountPage(),
      ),
    );
    renderV2('/v2/accounts?q=nothing');
    expect(await screen.findByText('No account matches')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Clear the filters' }));
    expect(await screen.findByRole('row', { name: /Synthetic Copper Works/ })).toBeInTheDocument();
    expect(window.location.search).toBe('');
  });

  it('shows the reasons the API refused a filter', async () => {
    server.use(v2Refuse('get', ACCOUNTS, 422, 'invalid_input', ['sort=mrr_desc needs a currency']));
    renderV2('/v2/accounts?sort=mrr_desc');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The server refused these filters.');
    expect(alert).toHaveTextContent('sort=mrr_desc needs a currency');
  });

  it('says so when the list cannot be loaded, and loads it again', async () => {
    let calls = 0;
    server.use(
      http.get(ACCOUNTS, () => {
        calls += 1;
        return calls === 1 ? HttpResponse.error() : HttpResponse.json(accountPage());
      }),
    );
    renderV2('/v2/accounts');
    expect(await screen.findByText(/accounts could not be loaded/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Try again/ }));
    expect(await screen.findByRole('row', { name: /Synthetic Copper Works/ })).toBeInTheDocument();
  });
});

describe('an account', () => {
  it('shows the subscription, each signal with its evidence, and the history', async () => {
    let asked = '';
    server.use(
      v2Respond('get', ACCOUNT, (request) => {
        asked = new URL(request.url).pathname;
        return accountDetail();
      }),
    );
    renderV2('/v2/account?id=DEMO-ACC-000188');
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Synthetic Copper Works #188' }),
    ).toBeInTheDocument();
    expect(asked).toBe('/api/v2/accounts/DEMO-ACC-000188');

    expect(screen.getByRole('note', { name: 'About this data' })).toHaveTextContent(
      '31 October 2026',
    );
    expect(screen.getByText('Where to look first.').parentElement).toHaveTextContent(
      'Prepare for the renewal on 2026-10-31.',
    );
    const signals = screen.getByRole('list', { name: 'Signals' });
    expect(signals).toHaveTextContent('Renewal approaching');
    expect(signals).toHaveTextContent('0 days');
    expect(screen.getByLabelText('Evidence: Renewal approaching')).toHaveTextContent(
      'renewal_date · 31 Oct 2026',
    );
    expect(screen.getByRole('list', { name: 'Rules not applied' })).toHaveTextContent(
      'sessions_decline: No snapshot 30 to 45 days earlier to compare with.',
    );
    expect(screen.getByText('365 days')).toBeInTheDocument();

    const history = screen.getByRole('table');
    const rows = within(history).getAllByRole('row');
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent('1 Sept 2026');
    expect(rows[1]).toHaveTextContent('No signals');
    expect(rows[2]).toHaveTextContent('Attention');
  });

  it('says so for an ID the dataset does not have', async () => {
    server.use(v2Refuse('get', ACCOUNT, 404, 'not_found'));
    renderV2('/v2/account?id=DEMO-ACC-999999');
    expect(await screen.findByText('There is no such account')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to the accounts' })).toHaveAttribute(
      'href',
      '/v2/accounts',
    );
  });

  it('asks for nothing without an ID', async () => {
    const seen = recordRequests();
    renderV2('/v2/account');
    expect(await screen.findByText('No account chosen')).toBeInTheDocument();
    expect(seen.filter((url) => url.pathname.startsWith('/api/v2'))).toEqual([]);
  });

  it('a server failure can be retried', async () => {
    let calls = 0;
    server.use(
      http.get('/api/v2/accounts/:id', () => {
        calls += 1;
        return calls === 1 ? HttpResponse.error() : HttpResponse.json(accountDetail());
      }),
    );
    renderV2('/v2/account?id=DEMO-ACC-000188');
    expect(await screen.findByText(/account could not be loaded/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Try again/ }));
    // Focus stays on the page's heading while the account loads again, and after.
    expect(screen.getByRole('heading', { level: 2, name: 'Account' })).toHaveFocus();
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Synthetic Copper Works #188' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'Account' })).toHaveFocus();
  });
});

describe('the requests the preview makes', () => {
  it('are the M1 and M2 routes only, across every page', async () => {
    const seen = recordRequests();
    server.use(
      v2Answer('get', SUMMARY, summary()),
      v2Answer('get', ACCOUNTS, accountPage()),
      v2Answer('get', ACCOUNT, accountDetail()),
    );
    renderV2('/v2');
    await screen.findByRole('heading', { level: 1, name: 'Retention workspace preview' });
    for (const [path, ready] of [
      ['/v2/data-hub', 'Choose a CSV file of account snapshots'],
      ['/v2/portfolio', 'Monthly recurring revenue by currency'],
      ['/v2/accounts', 'Account list'],
      ['/v2/account?id=DEMO-ACC-000188', 'Snapshot history'],
    ] as const) {
      act(() => navigate(path));
      expect(await screen.findByText(ready)).toBeInTheDocument();
    }
    const allowed = [
      /^\/api\/v2\/portfolio\/summary$/,
      /^\/api\/v2\/accounts$/,
      /^\/api\/v2\/accounts\/[^/]+$/,
    ];
    expect(seen.length).toBeGreaterThan(0);
    for (const url of seen) {
      expect(
        allowed.some((pattern) => pattern.test(url.pathname)),
        url.pathname,
      ).toBe(true);
    }
  });
});
