import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Overview } from '../../api/queries';
import { App } from '../../app/App';
import { modelInfo } from '../../test/fixtures';
import { overviewApi, QUEUE_ROWS } from '../../test/overviewApi';
import { customerPage, overview } from '../../test/overviewFixtures';
import { press } from '../../test/keyboard';
import { renderAt } from '../../test/render';
import { answer, recordRequests, refuse, respond, server } from '../../test/server';

/** The page at ``path``, its API answering from the fixtures. */
function showOverview(path = '/', book: Overview = overview()) {
  server.use(...overviewApi(book));
  return renderAt(<App />, path);
}

async function loaded() {
  return screen.findByRole('heading', {
    level: 1,
    name: 'Where retention risk sits in the book today',
  });
}

describe('Overview: the book at a glance', () => {
  it('states the book’s figures as the API gives them', async () => {
    showOverview();
    await loaded();
    expect(
      await screen.findByText(/^7,000 customers, scored by one calibrated model/),
    ).toBeInTheDocument();
    const figures = screen.getByRole('region', { name: 'Key figures' });
    expect(within(figures).getByText('7,000')).toBeInTheDocument();
    expect(within(figures).getByText('25.0%')).toBeInTheDocument();
    expect(within(figures).getByText(/1,400 of 5,600/)).toBeInTheDocument();
    expect(within(figures).getByText('$450,000')).toBeInTheDocument();
    expect(within(figures).getByText('1,500')).toBeInTheDocument();
    expect(within(figures).getByText('21.4% · $120,000 / mo')).toBeInTheDocument();
    expect(within(figures).getByText('Flagged at 0.36')).toBeInTheDocument();
    expect(screen.getByRole('contentinfo')).toHaveTextContent(
      'IBM Telco Customer Churn · 7,000 rows · attributions are signed SHAP, pp · orange raises, blue lowers',
    );
  });

  it('shows the operating point on the band scale', async () => {
    showOverview();
    await loaded();
    const scale = screen.getByRole('img', { name: /Probability scale/ });
    expect(scale).toHaveAccessibleName(
      'Probability scale: Low 0 to 25%, Moderate to 50%, High to 75%, Critical to 100%; flagged at 0.36 and above',
    );
    expect(within(scale).getByText('flag ≥ 0.36')).toBeInTheDocument();
    expect(within(scale).getByText('–100%')).toBeInTheDocument();
  });

  it('names the tenure card by what its figures show', async () => {
    showOverview('/', overview({ lifecycle: null, withheld_reason: 'the split is unverified.' }));
    await loaded();
    expect(screen.getByRole('region', { name: 'Churn by tenure' })).toBeInTheDocument();
  });

  it('withholds observed churn, saying why, when the partitions are unverified', async () => {
    const reason = 'the current dataset’s hash is unknown, so it cannot be matched.';
    showOverview('/', overview({ observed: null, withheld_reason: reason, campaign: null }));
    await loaded();
    const figures = screen.getByRole('region', { name: 'Key figures' });
    expect(within(figures).getByText('Withheld')).toBeInTheDocument();
    expect(
      screen.getByText((text) => text.includes(`Observed outcomes are withheld: ${reason}`)),
    ).toBeInTheDocument();
    const distribution = screen.getByRole('region', {
      name: 'Where the book sits on the probability scale',
    });
    expect(distribution).not.toHaveTextContent('dots');
  });

  it('reports a failure to load with a retry, never the server’s words', async () => {
    server.use(
      respond('get', '/api/overview', () => {
        throw new Error('server exploded');
      }),
    );
    renderAt(<App />, '/');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The overview could not be loaded.');
    expect(alert).not.toHaveTextContent('exploded');
  });

  it('keeps the reader’s place when the overview is asked for again', async () => {
    server.use(refuse('get', '/api/overview', 500, 'internal_error'));
    renderAt(<App />, '/');
    const alert = await screen.findByRole('alert');
    server.use(...overviewApi());
    // The button goes as the overview loads again: the reader continues at the page.
    await press(within(alert).getByRole('button', { name: 'Try again' }));
    expect(document.getElementById('main')).toHaveFocus();
    await loaded();
  });
});

describe('Overview: header', () => {
  it('opens the provenance of the data and the model', async () => {
    showOverview();
    await loaded();
    await userEvent.click(await screen.findByRole('button', { name: 'Provenance verified' }));
    const panel = screen.getByRole('dialog', { name: 'Data provenance' });
    expect(panel).toHaveTextContent(`${'b'.repeat(8)}…${'b'.repeat(4)}`);
    expect(panel).toHaveTextContent(`${'a'.repeat(8)}…${'a'.repeat(4)}`);
    expect(panel).toHaveTextContent('matches model');
    expect(panel).toHaveTextContent(`${window.location.host} · local only`);
  });

  it('says plainly when provenance is not verified, and why', async () => {
    server.use(
      answer(
        'get',
        '/api/model-info',
        modelInfo({
          provenance: {
            verified: false,
            data_sha256: null,
            split_digest: null,
            withheld_reason: 'the dataset could not be hashed.',
          },
        }),
      ),
    );
    showOverview();
    await loaded();
    await userEvent.click(await screen.findByRole('button', { name: 'Provenance not verified' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('the dataset could not be hashed.');
  });

  it('searches the queue by customer ID, keeping only characters an ID can hold', async () => {
    showOverview();
    await loaded();
    const seen = recordRequests();
    await userEvent.type(
      screen.getByRole('searchbox', { name: 'Find a customer by ID' }),
      'de<mo-1',
    );
    expect(screen.getByRole('searchbox')).toHaveValue('demo-1');
    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get('q')).toBe('demo-1'),
    );
    await waitFor(() =>
      expect(seen.some((url) => url.searchParams.get('q') === 'demo-1')).toBe(true),
    );
  });

  it('exports the scored book as a file from the API', async () => {
    showOverview();
    await loaded();
    const link = screen.getByRole('link', { name: 'Export scored CSV' });
    expect(link).toHaveAttribute('href', '/api/customers/export');
    expect(link).toHaveAttribute('download');
  });
});

describe('Overview: retention queue', () => {
  function queue() {
    return screen.getByRole('region', { name: /Retention queue/ });
  }
  function lastListing(seen: URL[]) {
    return seen
      .filter((url) => url.pathname === '/api/customers' && !url.searchParams.has('ids'))
      .at(-1);
  }

  it('links to every flagged customer in the explorer, which reads the same address', async () => {
    showOverview();
    await loaded();
    const link = await within(queue()).findByRole('link', {
      name: /^See all 2,300 in the explorer/,
    });
    expect(link).toHaveAttribute('href', '/explorer?flagged=true');
  });

  it('lists flagged customers in every band that holds them, by revenue at risk', async () => {
    const seen = recordRequests();
    showOverview();
    expect(
      await within(await screen.findByRole('table', { name: 'Flagged customers' })).findByText(
        'DEMO-0001',
      ),
    ).toBeInTheDocument();
    const url = lastListing(seen);
    expect(url?.searchParams.get('flagged')).toBe('true');
    expect(url?.searchParams.getAll('bands')).toEqual(['Moderate', 'High', 'Critical']);
    expect(url?.searchParams.get('sort')).toBe('at_risk');
    expect(url?.searchParams.get('dir')).toBe('desc');
    expect(url?.searchParams.get('page_size')).toBe('8');
    expect(queue()).toHaveTextContent('2,300 flagged in scope');
    expect(queue()).toHaveTextContent('Showing 1–8 of 2,300 flagged');
    expect(within(queue()).getByRole('link', { name: 'Explain DEMO-0001' })).toHaveAttribute(
      'href',
      '/customer?id=DEMO-0001',
    );
  });

  it('explains the page’s customers in one request and shows their two strongest attributions', async () => {
    const seen = recordRequests();
    showOverview();
    const table = await screen.findByRole('table', { name: 'Flagged customers' });
    const first = (await within(table).findByText('DEMO-0001')).closest(
      '[role="row"]',
    ) as HTMLElement;
    expect(await within(first).findByText('Tenure +31.7')).toBeInTheDocument();
    expect(within(first).getByText('Monthly −20.9')).toBeInTheDocument();
    expect(within(first).queryByText(/Contract/)).not.toBeInTheDocument();
    expect(seen.filter((url) => url.pathname === '/api/explain')).toHaveLength(1);
  });

  it('marks attributions as unavailable when the explainer refuses', async () => {
    const seen = recordRequests();
    showOverview();
    server.use(refuse('post', '/api/explain', 503, 'explanations_busy'));
    const table = await screen.findByRole('table', { name: 'Flagged customers' });
    expect(
      await within(queue()).findByText(
        'Attributions are unavailable right now.',
        {},
        { timeout: 3000 },
      ),
    ).toBeInTheDocument();
    // A busy explainer is asked once more by itself before the queue says so.
    expect(seen.filter((url) => url.pathname === '/api/explain')).toHaveLength(2);
    expect(table).toBeInTheDocument();
  });

  it('filters by band chip, and asks for nothing when no band is left', async () => {
    const seen = recordRequests();
    showOverview();
    await screen.findByRole('table', { name: 'Flagged customers' });
    await userEvent.click(within(queue()).getByRole('button', { name: /^High/ }));
    expect(new URLSearchParams(window.location.search).getAll('bands')).toEqual([
      'Moderate',
      'Critical',
    ]);
    await waitFor(() =>
      expect(lastListing(seen)?.searchParams.getAll('bands')).toEqual(['Moderate', 'Critical']),
    );

    await userEvent.click(within(queue()).getByRole('button', { name: /^Moderate/ }));
    const before = seen.length;
    await userEvent.click(within(queue()).getByRole('button', { name: /^Critical/ }));
    expect(await within(queue()).findByText('No band selected')).toBeInTheDocument();
    expect(seen.slice(before).filter((url) => url.pathname === '/api/customers')).toEqual([]);
  });

  it('forgets a band choice that is the default again', async () => {
    showOverview();
    await screen.findByRole('table', { name: 'Flagged customers' });
    await userEvent.click(within(queue()).getByRole('button', { name: /^High/ }));
    await userEvent.click(within(queue()).getByRole('button', { name: /^High/ }));
    expect(new URLSearchParams(window.location.search).has('bands')).toBe(false);
    expect(
      within(queue()).queryByRole('button', { name: 'Reset filters' }),
    ).not.toBeInTheDocument();
  });

  it('says so when the threshold flags nobody, and asks for nothing', async () => {
    const seen = recordRequests();
    const book = overview();
    showOverview('/', { ...book, bands: book.bands.map((band) => ({ ...band, flagged: 0 })) });
    await loaded();
    expect(await within(queue()).findByText('Nobody is flagged')).toBeInTheDocument();
    expect(
      within(queue()).getByText('No customer reaches the 0.36 threshold.'),
    ).toBeInTheDocument();
    expect(seen.filter((url) => url.pathname === '/api/customers')).toEqual([]);
  });

  it('opens the queue on the riskiest bands from the key figures, with no other filter', async () => {
    const seen = recordRequests();
    showOverview('/?part=test&q=12');
    await loaded();
    await userEvent.click(screen.getByRole('link', { name: /^High or Critical:/ }));
    expect(new URLSearchParams(window.location.search).has('part')).toBe(false);
    expect(new URLSearchParams(window.location.search).has('q')).toBe(false);
    // Focus follows the page to the queue, and the queue says what it now holds.
    expect(screen.getByRole('heading', { name: /^Retention queue/ })).toHaveFocus();
    expect(
      await within(queue()).findByText('2,300 flagged customers in scope.'),
    ).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).getAll('bands')).toEqual([
      'High',
      'Critical',
    ]);
    await waitFor(() =>
      expect(lastListing(seen)?.searchParams.getAll('bands')).toEqual(['High', 'Critical']),
    );
  });

  it('sorts by a column and pages through the queue', async () => {
    const seen = recordRequests();
    showOverview();
    await screen.findByRole('table', { name: 'Flagged customers' });
    await userEvent.click(within(queue()).getByRole('button', { name: 'Tenure' }));
    expect(window.location.search).toContain('sort=tenure');
    expect(window.location.search).toContain('dir=asc');
    await userEvent.click(within(queue()).getByRole('button', { name: 'Next page' }));
    expect(new URLSearchParams(window.location.search).get('page')).toBe('2');
    await waitFor(() => expect(lastListing(seen)?.searchParams.get('page')).toBe('2'));
  });

  it('keeps the page a link opens on: the search box writes nothing until something is typed', async () => {
    const seen = recordRequests();
    showOverview('/?page=2');
    await screen.findByRole('table', { name: 'Flagged customers' });
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(window.location.search).toBe('?page=2');
    expect(lastListing(seen)?.searchParams.get('page')).toBe('2');
  });

  it('filters by partition', async () => {
    const seen = recordRequests();
    showOverview();
    await screen.findByRole('table', { name: 'Flagged customers' });
    await userEvent.selectOptions(
      within(queue()).getByRole('combobox', { name: 'Partition' }),
      'test',
    );
    expect(new URLSearchParams(window.location.search).get('part')).toBe('test');
    await waitFor(() => expect(lastListing(seen)?.searchParams.get('partition')).toBe('test'));
  });

  it('totals a selection on the server and exports exactly it', async () => {
    const seen = recordRequests();
    showOverview();
    const table = await screen.findByRole('table', { name: 'Flagged customers' });
    await userEvent.click(await within(table).findByRole('checkbox', { name: 'Select DEMO-0001' }));
    await userEvent.click(within(table).getByRole('checkbox', { name: 'Select DEMO-0002' }));
    const bar = screen.getByRole('region', { name: 'Bulk actions' });
    expect(bar).toHaveTextContent('2 selected');
    expect(await within(bar).findByText('$123.45 / mo at risk')).toBeInTheDocument();
    const selection = seen.filter((url) => url.searchParams.has('ids')).at(-1);
    expect(selection?.searchParams.getAll('ids')).toEqual(['DEMO-0001', 'DEMO-0002']);
    expect(selection?.searchParams.has('flagged')).toBe(false);
    expect(within(bar).getByRole('link', { name: 'Export selection' })).toHaveAttribute(
      'href',
      '/api/customers/export?ids=DEMO-0001&ids=DEMO-0002',
    );
    await userEvent.click(within(bar).getByRole('button', { name: 'Clear' }));
    expect(screen.queryByRole('region', { name: 'Bulk actions' })).not.toBeInTheDocument();
  });

  it('never shows the last selection’s total for a new selection', async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => (release = resolve));
    showOverview();
    server.use(
      respond('get', '/api/customers', async (request) => {
        const ids = new URL(request.url).searchParams.getAll('ids');
        if (ids.length > 1) await gate;
        const page = customerPage(QUEUE_ROWS);
        return { ...page, summary: { ...page.summary, at_risk: 10 * Math.max(ids.length, 1) } };
      }),
    );
    const table = await screen.findByRole('table', { name: 'Flagged customers' });
    await userEvent.click(await within(table).findByRole('checkbox', { name: 'Select DEMO-0001' }));
    const bar = screen.getByRole('region', { name: 'Bulk actions' });
    expect(await within(bar).findByText('$10.00 / mo at risk')).toBeInTheDocument();
    await userEvent.click(within(table).getByRole('checkbox', { name: 'Select DEMO-0002' }));
    expect(within(bar).queryByText('$10.00 / mo at risk')).not.toBeInTheDocument();
    release();
    expect(await within(bar).findByText('$20.00 / mo at risk')).toBeInTheDocument();
  });

  it('shows the search as a chip that clears it, and the box with it', async () => {
    showOverview('/?q=DEMO');
    await screen.findByRole('table', { name: 'Flagged customers' });
    expect(screen.getByRole('searchbox')).toHaveValue('DEMO');
    await userEvent.click(within(queue()).getByRole('button', { name: /^Clear search/ }));
    expect(new URLSearchParams(window.location.search).has('q')).toBe(false);
    expect(screen.getByRole('searchbox')).toHaveValue('');
  });
});

describe('Overview: the queue keeps the keyboard’s place', () => {
  function queue() {
    return screen.getByRole('region', { name: /Retention queue/ });
  }
  function heading() {
    return screen.getByRole('heading', { name: /^Retention queue/ });
  }
  /** The empty state titled ``title``: its own "Reset filters", not the bar's. */
  function emptyState(title: string) {
    const state = within(queue()).getByText(title).closest<HTMLElement>('[role="status"]');
    if (!state) throw new Error(`no empty state titled ${title}`);
    return state;
  }

  it('moves the focus to the queue when the bar’s reset goes away with the filters', async () => {
    showOverview('/?part=train');
    await screen.findByRole('table', { name: 'Flagged customers' });
    await press(within(queue()).getByRole('button', { name: 'Reset filters' }));
    expect(new URLSearchParams(window.location.search).has('part')).toBe(false);
    expect(
      within(queue()).queryByRole('button', { name: 'Reset filters' }),
    ).not.toBeInTheDocument();
    expect(document.activeElement).not.toBe(document.body);
    expect(heading()).toHaveFocus();
  });

  it('moves the focus to the queue when no band was left and the reset brings them back', async () => {
    showOverview('/?bands=');
    await screen.findByText('No band selected');
    await press(
      within(emptyState('No band selected')).getByRole('button', { name: 'Reset filters' }),
    );
    expect(new URLSearchParams(window.location.search).has('bands')).toBe(false);
    expect(await screen.findByRole('table', { name: 'Flagged customers' })).toBeInTheDocument();
    expect(heading()).toHaveFocus();
  });

  it('moves the focus to the queue when nobody matched and the reset clears the search', async () => {
    server.use(...overviewApi());
    server.use(
      respond('get', '/api/customers', (request) =>
        new URL(request.url).searchParams.has('q')
          ? customerPage([])
          : customerPage(QUEUE_ROWS, { total: 2300, pages: 288 }),
      ),
    );
    renderAt(<App />, '/?q=NOBODY');
    await screen.findByText('No flagged customer matches');
    await press(
      within(emptyState('No flagged customer matches')).getByRole('button', {
        name: 'Reset filters',
      }),
    );
    expect(new URLSearchParams(window.location.search).has('q')).toBe(false);
    expect(await screen.findByRole('table', { name: 'Flagged customers' })).toBeInTheDocument();
    expect(heading()).toHaveFocus();
  });

  it('moves the focus to the queue when the search chip clears the search', async () => {
    showOverview('/?q=DEMO');
    await screen.findByRole('table', { name: 'Flagged customers' });
    await press(within(queue()).getByRole('button', { name: /^Clear search/ }));
    expect(new URLSearchParams(window.location.search).has('q')).toBe(false);
    expect(heading()).toHaveFocus();
  });

  it('keeps the focus in the queue when a failed page is asked for again', async () => {
    server.use(...overviewApi());
    server.use(refuse('get', '/api/customers', 500, 'internal_error'));
    renderAt(<App />, '/');
    await loaded();
    const alert = await within(queue()).findByRole('alert');
    expect(alert).toHaveTextContent('The queue could not be loaded.');
    server.use(...overviewApi());
    await press(within(alert).getByRole('button', { name: 'Try again' }));
    expect(heading()).toHaveFocus();
    expect(await screen.findByRole('table', { name: 'Flagged customers' })).toBeInTheDocument();
  });

  it('keeps the focus on the pager’s last button at the last page', async () => {
    server.use(...overviewApi());
    server.use(
      answer('get', '/api/customers', customerPage(QUEUE_ROWS, { page: 3, pages: 3, total: 20 })),
    );
    renderAt(<App />, '/?page=3');
    await screen.findByRole('table', { name: 'Flagged customers' });
    const next = within(queue()).getByRole('button', { name: 'Next page' });
    expect(next).toHaveAttribute('aria-disabled', 'true');
    await press(next);
    expect(new URLSearchParams(window.location.search).get('page')).toBe('3');
    expect(next).toHaveFocus();
  });
});

describe('Overview: where the book sits', () => {
  function card() {
    return screen.getByRole('region', { name: 'Where the book sits on the probability scale' });
  }

  it('summarises each band, and switches the measure to monthly charges', async () => {
    showOverview();
    await loaded();
    const low = within(card()).getByRole('button', { name: /^Low 0–0.25 4,000/ });
    expect(low).toHaveAccessibleDescription('Filters the queue to this band.');
    expect(low).toHaveTextContent('4,000');
    expect(low).toHaveTextContent('57.1%');
    expect(low).toHaveTextContent('churned 8.0%');
    expect(low).toHaveTextContent('flagged none');
    await userEvent.click(within(card()).getByRole('button', { name: 'Monthly charges' }));
    expect(low).toHaveTextContent('$220.0k');
    expect(low).toHaveTextContent('48.9%');
  });

  it('filters the queue to one band, and back to every flagged band', async () => {
    showOverview();
    await loaded();
    const high = within(card()).getByRole('button', { name: /^High / });
    await userEvent.click(high);
    expect(new URLSearchParams(window.location.search).getAll('bands')).toEqual(['High']);
    expect(high).toHaveAttribute('aria-pressed', 'true');
    expect(high).toHaveAccessibleDescription('Shows every flagged band in the queue again.');
    await userEvent.click(high);
    expect(new URLSearchParams(window.location.search).has('bands')).toBe(false);
  });

  it('gives the histogram’s figures as a table, small bins without a rate', async () => {
    showOverview();
    await loaded();
    const table = within(card()).getByRole('table', { name: 'Customers per 0.05 of probability' });
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(21);
    expect(rows[1]).toHaveTextContent('0.00–0.05');
    expect(rows[1]).toHaveTextContent('350');
    expect(rows[1]).toHaveTextContent('5.0% (14 of 280)');
    expect(rows[20]).toHaveTextContent('fewer than 20 with outcomes');
  });

  it('walks the bins with the keyboard and filters the queue to a bin’s band', async () => {
    showOverview();
    await loaded();
    const bins = within(card()).getByRole('slider', { name: 'Probability bin' });
    bins.focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(bins).toHaveAttribute(
      'aria-valuetext',
      'p 0.05–0.10, Low: 350 customers, $22,500 a month, observed 10.0% (28 of 280)',
    );
    const tip = within(card()).getByText('p 0.05–0.10 · Low').parentElement;
    expect(tip).toHaveAttribute('aria-hidden', 'true');
    expect(tip).toHaveTextContent('350 customers · $22,500/mo');
    await userEvent.keyboard('{Enter}');
    expect(new URLSearchParams(window.location.search).getAll('bands')).toEqual(['Low']);
    expect(await screen.findByText('Nobody in the Low band is flagged')).toBeInTheDocument();
    expect(bins).toHaveAttribute('aria-valuetext', expect.stringMatching(/^p 0.05–0.10, Low/));
    expect(
      screen.getByText(
        'Low tops out at a probability of 0.25, below the 0.36 threshold, so these 4,000 customers never enter the queue.',
      ),
    ).toBeInTheDocument();
  });
});

describe('Overview: simulated campaign', () => {
  it('shows the campaign at the shipped threshold, marked as simulated', async () => {
    showOverview();
    await loaded();
    const card = screen.getByRole('region', { name: /Campaign at 0.36/ });
    expect(within(card).getByText('SIMULATED')).toBeInTheDocument();
    expect(within(card).getByText('Net benefit on validation').parentElement).toHaveTextContent(
      '$26,400',
    );
    expect(within(card).getByText('0.36 shipped · 0.30 peak, $32 higher')).toBeInTheDocument();
    expect(within(card).getByText('450')).toBeInTheDocument();
    expect(within(card).getByText('/ 1,400')).toBeInTheDocument();
    expect(within(card).getByText('0.580 · 0.695')).toBeInTheDocument();
    expect(within(card).getByText('$22,500')).toBeInTheDocument();
    expect(within(card).getByText('$48,900')).toBeInTheDocument();
    expect(within(card).getByText(/^Assumed: offer \$50, success 30%/)).toBeInTheDocument();
    expect(
      within(card).getByRole('img', { name: /^Simulated net benefit by threshold/ }),
    ).toBeInTheDocument();
    expect(within(card).getByRole('link', { name: 'Open simulator' })).toHaveAttribute(
      'href',
      '/simulator',
    );
  });

  it('says why there is no campaign when outcomes are withheld', async () => {
    showOverview('/', overview({ campaign: null, withheld_reason: 'the split is unverified.' }));
    await loaded();
    const card = screen.getByRole('region', { name: /Campaign/ });
    expect(card).toHaveTextContent('needs observed outcomes on the validation partition');
  });
});

describe('Overview: what the risk is associated with', () => {
  it('breaks churn down by segment on the training partition', async () => {
    showOverview();
    await loaded();
    const card = screen.getByRole('region', { name: 'Churn by segment' });
    expect(within(card).getByText('TRAIN · 4,200')).toBeInTheDocument();
    const panel = within(card).getByRole('tabpanel', { name: 'Contract' });
    expect(panel).toHaveTextContent('Month-to-month');
    expect(panel).toHaveTextContent('1,000 of 2,300');
    expect(panel).toHaveTextContent('43.5%');
    expect(card).toHaveTextContent('A 17× spread, partly self-selection');
    expect(card).toHaveTextContent('Tick = 25.0% average.');

    await userEvent.click(within(card).getByRole('tab', { name: 'Payment' }));
    const payment = within(card).getByRole('tabpanel', { name: 'Payment' });
    expect(payment).toHaveTextContent('Credit card (auto)');
    expect(payment).toHaveTextContent('fewer than 20 customers');

    await userEvent.click(within(card).getByRole('tab', { name: 'Price' }));
    expect(within(card).getByRole('tabpanel', { name: 'Price' })).toHaveTextContent('Under $35');
    expect(card).toHaveTextContent('Not monotonic: churn peaks at $75–95.');
  });

  it('shows when in the relationship customers left', async () => {
    showOverview();
    await loaded();
    const card = screen.getByRole('region', { name: 'Risk is front-loaded' });
    expect(card).toHaveTextContent('median tenure 10 mo churned · 37 retained');
    const first = within(card).getByRole('button', {
      name: '0–3 months: 360 of 640 churned, 56.3%',
    });
    act(() => first.focus());
    // Drawn for the eye; the bar's own name already says it.
    expect(within(card).getByText('0–3 months').parentElement).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    expect(card).toHaveTextContent('avg 25.0%');
  });

  it('ranks what moves the score, in the model and on its own', async () => {
    showOverview();
    await loaded();
    const card = screen.getByRole('region', { name: 'What moves the score' });
    expect(card).toHaveTextContent(
      'Mean |SHAP| on 300 validation customers, in percentage points.',
    );
    expect(card).toHaveTextContent('Tenure14.9 pp');
    expect(card).not.toHaveTextContent('Payment method');
    expect(card).toHaveTextContent('Contract ranks first on its own but fourth in the model');
    await userEvent.click(within(card).getByRole('button', { name: 'On its own' }));
    expect(card).toHaveTextContent('Contract typeV 0.419');
    expect(card).toHaveTextContent('Tenure|r| 0.353');
  });
});
