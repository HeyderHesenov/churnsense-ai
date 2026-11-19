import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Overview } from '../../api/queries';
import { App } from '../../app/App';
import { entriesAdded } from '../../test/history';
import { overviewApi, QUEUE_ROWS } from '../../test/overviewApi';
import { explanations, overview } from '../../test/overviewFixtures';
import { contractByTenure, hidden, reported } from '../../test/patternsApi';
import { renderAt } from '../../test/render';
import { answer, recordRequests, refuse, respond, server } from '../../test/server';
import { CAUSAL } from '../../test/wording';

function showOverview(path = '/', book: Overview = overview()) {
  server.use(...overviewApi(book));
  return renderAt(<App />, path);
}

async function loaded() {
  return screen.findByRole('heading', { level: 1, name: 'Where retention risk sits in the book' });
}

function tab(name: RegExp) {
  return screen.getByRole('tab', { name });
}

function region(name: string | RegExp) {
  return screen.getByRole('region', { name });
}

/** Lets every request the page has started reach the mock server. */
async function settle() {
  await act(() => new Promise((resolve) => setTimeout(resolve, 200)));
}

describe('Overview: tabs', () => {
  it('opens on the summary, and reads any other tab from the address', async () => {
    showOverview();
    await loaded();
    const tabs = within(screen.getByRole('tablist', { name: 'Overview sections' })).getAllByRole(
      'tab',
    );
    expect(tabs.map((t) => t.textContent)).toEqual([
      '1Summary The book at a glance',
      '2Risk distribution Where customers sit',
      '3Associations What risk goes with',
      '4Retention queue Who to contact first 1,900 in the queue',
    ]);
    expect(tab(/^Summary/)).toHaveAttribute('aria-selected', 'true');
    expect(region('Key figures')).toBeInTheDocument();
  });

  it.each([
    ['distribution', 'Where the book sits on the probability scale'],
    ['associations', 'Churn by segment'],
    ['queue', /^Retention queue/],
  ])('shows the %s tab alone when the address names it', async (value, name) => {
    showOverview(`/?tab=${value}`);
    await loaded();
    expect(region(name)).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Key figures' })).toBeNull();
  });

  it('reads an unknown tab as the summary', async () => {
    showOverview('/?tab=nonsense');
    await loaded();
    expect(tab(/^Summary/)).toHaveAttribute('aria-selected', 'true');
  });

  it('steps through the tabs with Back and Forward, the queue’s filters kept', async () => {
    showOverview('/?bands=High');
    await loaded();
    await userEvent.click(tab(/^Risk distribution/));
    await userEvent.click(tab(/^Associations/));
    await userEvent.click(tab(/^Retention queue/));
    expect(window.location.search).toBe('?bands=High&tab=queue');
    act(() => window.history.back());
    await waitFor(() => expect(tab(/^Associations/)).toHaveAttribute('aria-selected', 'true'));
    act(() => window.history.back());
    await waitFor(() => expect(tab(/^Risk distribution/)).toHaveAttribute('aria-selected', 'true'));
    act(() => window.history.back());
    await waitFor(() => expect(tab(/^Summary/)).toHaveAttribute('aria-selected', 'true'));
    act(() => window.history.forward());
    await waitFor(() => expect(tab(/^Risk distribution/)).toHaveAttribute('aria-selected', 'true'));
    expect(new URLSearchParams(window.location.search).getAll('bands')).toEqual(['High']);
  });

  it('moves between tabs with the arrow keys', async () => {
    showOverview();
    await loaded();
    act(() => tab(/^Summary/).focus());
    await userEvent.keyboard('{ArrowRight}');
    expect(tab(/^Risk distribution/)).toHaveFocus();
    expect(tab(/^Risk distribution/)).toHaveAttribute('aria-selected', 'true');
    await userEvent.keyboard('{ArrowLeft}{ArrowLeft}');
    expect(tab(/^Retention queue/)).toHaveAttribute('aria-selected', 'true');
  });

  it('opens the queue from a search typed on the summary in one history entry', async () => {
    showOverview();
    await loaded();
    const added = entriesAdded();
    await userEvent.type(screen.getByRole('searchbox', { name: 'Find a customer by ID' }), 'DEMO');
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('q')).toBe('DEMO'));
    expect(new URLSearchParams(window.location.search).get('tab')).toBe('queue');
    await userEvent.type(screen.getByRole('searchbox'), '-1');
    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get('q')).toBe('DEMO-1'),
    );
    expect(added()).toBe(1);
  });
});

describe('Overview summary: key figures', () => {
  it('draws a small picture beside each figure, hidden from screen readers', async () => {
    showOverview();
    await loaded();
    const figures = region('Key figures');
    const pictures = figures.querySelectorAll('[aria-hidden="true"] > svg');
    expect(pictures).toHaveLength(5);
    // The histogram's bars are the API's bins, one each.
    expect(pictures[0]?.querySelectorAll('rect')).toHaveLength(20);
  });

  it('draws no observed churn while outcomes are withheld', async () => {
    showOverview('/', overview({ observed: null, withheld_reason: 'the split is unverified.' }));
    await loaded();
    const tile = within(region('Key figures')).getByText('Observed churn rate').parentElement;
    expect(tile?.querySelector('svg')).toBeNull();
  });

  it('leaves a band’s bar out, never at zero, when the API publishes no rate for it', async () => {
    const book = overview();
    showOverview('/', {
      ...book,
      bands: book.bands.map((band) =>
        band.name === 'Critical' ? { ...band, observed: null } : band,
      ),
    });
    await loaded();
    const tile = within(region('Key figures')).getByText('Observed churn rate').parentElement;
    const bars = [...(tile?.querySelectorAll('rect[data-band]') ?? [])];
    expect(bars.map((bar) => bar.getAttribute('data-band'))).toEqual(['Low', 'Moderate', 'High']);
  });

  it('draws no ring for a share the API does not give', async () => {
    const book = overview();
    showOverview('/', { ...book, flagged: { ...book.flagged, in_queue_share: null } });
    await loaded();
    const tile = screen.getByRole('link', { name: /^Flagged at 0.36, in the queue/ });
    expect(tile.querySelector('circle')).toBeNull();
  });
});

describe('Overview summary: risk at a glance', () => {
  it('rings the book by band and states everyone flagged, as the API gives them', async () => {
    showOverview();
    await loaded();
    const card = region('Risk at a glance');
    expect(within(card).getByRole('img')).toHaveAccessibleName(
      /^Customers by risk band: Low 4,000 \(57\.1%\), .*\. 2,300 flagged at 0\.36, 32\.9% of the book\.$/,
    );
    expect(card.querySelectorAll('circle[data-band]')).toHaveLength(4);
    const rows = within(card).getAllByRole('listitem');
    expect(rows[0]).toHaveTextContent('Low0–0.254,0008.0% churned');
    expect(
      within(card).getByRole('link', { name: /Explore the full distribution/ }),
    ).toHaveAttribute('href', '/?tab=distribution');
  });

  it('draws no arc for a band whose share the API does not give', async () => {
    const book = overview();
    showOverview('/', {
      ...book,
      bands: book.bands.map((band) => (band.name === 'Low' ? { ...band, share: null } : band)),
    });
    await loaded();
    expect(region('Risk at a glance').querySelectorAll('circle[data-band]')).toHaveLength(3);
  });

  it('says withheld rather than a rate while outcomes are withheld', async () => {
    showOverview('/', overview({ withheld_reason: 'the split is unverified.' }));
    await loaded();
    const rows = within(region('Risk at a glance')).getAllByRole('listitem');
    for (const row of rows) expect(row).toHaveTextContent(/withheld$/);
  });
});

describe('Overview summary: briefing', () => {
  it('states four findings, each from the API, each leading somewhere real', async () => {
    showOverview();
    await loaded();
    const card = region('Briefing');
    await within(card).findByText(/Expected calibration error 0\.031/);
    const items = within(card).getAllByRole('listitem');
    expect(items).toHaveLength(4);
    expect(items[0]).toHaveTextContent(
      '32.9% of the book is flagged2,300 customers at or above 0.36; 1,900 of them in the retention queue.',
    );
    expect(items[1]).toHaveTextContent(
      'Median tenure 10 months among customers who churned, 37 among those who stayed (training partition).',
    );
    expect(items[2]).toHaveTextContent('VALIDATION');
    expect(items[3]).toHaveTextContent('A campaign pays, on paper');
    expect(items[3]).toHaveTextContent('SIMULATED');
    expect(items[3]).toHaveTextContent(
      '$26,400 simulated net benefit at 0.36 on validation, 450 offers.',
    );
    const hrefs = within(card)
      .getAllByRole('link')
      .map((link) => link.getAttribute('href'));
    expect(hrefs).toEqual(['/?tab=queue', '/patterns', '/performance', '/simulator']);
    // No ratio the API does not give, and no causal word.
    expect(card).not.toHaveTextContent(/\b\d+ in \d+\b/);
    expect(card.textContent).not.toMatch(CAUSAL);
  });

  it('leaves out a finding whose figure the API does not give', async () => {
    server.use(refuse('get', '/api/performance', 500, 'internal_error'));
    showOverview('/', overview({ campaign: null, lifecycle: null }));
    await loaded();
    const card = region('Briefing');
    // The performance query is asked once more by itself; then the row stays out.
    await waitFor(() => expect(within(card).getAllByRole('listitem')).toHaveLength(1));
    expect(card).not.toHaveTextContent('calibration');
    expect(card).not.toHaveTextContent('campaign');
  });

  it('says plainly when the simulated campaign does not pay', async () => {
    const book = overview();
    if (!book.campaign) throw new Error('the fixture has a campaign');
    showOverview('/', { ...book, campaign: { ...book.campaign, net_benefit: -1200 } });
    await loaded();
    expect(region('Briefing')).toHaveTextContent('A campaign does not pay at these assumptions');
  });

  it('opens the queue tab with the focus on its heading', async () => {
    showOverview();
    await loaded();
    await userEvent.click(
      within(region('Briefing')).getByRole('link', { name: 'Open the retention queue' }),
    );
    expect(tab(/^Retention queue/)).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('heading', { name: /^Retention queue/ })).toHaveFocus();
  });
});

describe('Overview summary: customers to call first', () => {
  it('shows the queue’s first three from the queue’s own listing, and asks for no explanation', async () => {
    const seen = recordRequests();
    showOverview();
    await loaded();
    const card = region(/^Customers to call first/);
    const links = await within(card).findAllByRole('link', { name: /^DEMO-/ });
    expect(links.map((link) => link.getAttribute('href'))).toEqual(
      QUEUE_ROWS.slice(0, 3).map((row) => `/customer?id=${row.id}`),
    );
    const listings = () =>
      seen.filter((url) => url.pathname === '/api/customers' && !url.searchParams.has('ids'));
    const explains = () => seen.filter((url) => url.pathname === '/api/explain');
    expect(listings()).toHaveLength(1);
    const url = listings()[0];
    expect(url?.searchParams.get('flagged')).toBe('true');
    expect(url?.searchParams.get('exclude_churned')).toBe('true');
    expect(url?.searchParams.get('sort')).toBe('at_risk');
    expect(url?.searchParams.get('dir')).toBe('desc');
    expect(url?.searchParams.get('page_size')).toBe('8');
    // No attribution here, and none on its way: the card points to where they are.
    expect(within(card).queryByText('Tenure +31.7')).toBeNull();
    expect(within(card).queryByText('Loading attributions')).toBeNull();
    expect(
      within(card).getByRole('link', { name: 'View explanations in Retention Queue' }),
    ).toHaveAttribute('href', '/?tab=queue');
    // One way to the queue from the card: the customers' own links go to each customer.
    expect(
      within(card)
        .getAllByRole('link')
        .filter((link) => !link.getAttribute('href')?.startsWith('/customer?')),
    ).toHaveLength(1);
    expect(within(card).queryByRole('link', { name: /^See all/ })).toBeNull();
    await settle();
    expect(explains()).toEqual([]);

    await userEvent.click(
      within(card).getByRole('link', { name: 'View explanations in Retention Queue' }),
    );
    const queue = await screen.findByRole('table', { name: 'Flagged customers' });
    expect(await within(queue).findAllByText('Tenure +31.7')).not.toHaveLength(0);
    // The queue tab reuses the summary's listing and asks for its page's explanations once.
    expect(listings()).toHaveLength(1);
    expect(explains()).toHaveLength(1);
  });

  it('opens the queue tab from its explanations link, with the focus on the queue’s heading', async () => {
    const seen = recordRequests();
    showOverview();
    await loaded();
    const card = region(/^Customers to call first/);
    await within(card).findAllByRole('link', { name: /^DEMO-/ });
    await userEvent.click(
      within(card).getByRole('link', { name: 'View explanations in Retention Queue' }),
    );
    expect(tab(/^Retention queue/)).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('heading', { name: /^Retention queue/ })).toHaveFocus();
    await waitFor(() =>
      expect(seen.filter((url) => url.pathname === '/api/explain')).toHaveLength(1),
    );
  });

  it('sends the queue’s explanation once, however often the tabs change before or after it is answered', async () => {
    const seen = recordRequests();
    let release = () => {};
    const answered = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(...overviewApi());
    server.use(
      respond('post', '/api/explain', async (request) => {
        const body = (await request.json()) as { customer_ids: string[] };
        await answered;
        return explanations(body.customer_ids);
      }),
    );
    renderAt(<App />, '/');
    await loaded();
    await within(region(/^Customers to call first/)).findAllByRole('link', { name: /^DEMO-/ });
    const explains = () => seen.filter((url) => url.pathname === '/api/explain');
    await settle();
    expect(explains()).toEqual([]);
    await userEvent.click(tab(/^Retention queue/));
    await waitFor(() => expect(explains()).toHaveLength(1));
    // Away and back while it is unanswered: the request is neither cancelled nor sent again.
    await userEvent.click(tab(/^Risk distribution/));
    await userEvent.click(tab(/^Summary/));
    await userEvent.click(tab(/^Retention queue/));
    act(() => release());
    const queue = await screen.findByRole('table', { name: 'Flagged customers' });
    expect(await within(queue).findAllByText('Tenure +31.7')).not.toHaveLength(0);
    // And once answered, every tab again: the cached answer stands.
    for (const name of [/^Summary/, /^Associations/, /^Retention queue/]) {
      await userEvent.click(tab(name));
    }
    expect(within(queue).getAllByText('Tenure +31.7')).not.toHaveLength(0);
    expect(explains()).toHaveLength(1);
  });

  it('sends the queue’s explanation once when the reader leaves the page before it is answered and comes Back', async () => {
    const seen = recordRequests();
    let release = () => {};
    const answered = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(...overviewApi());
    server.use(
      respond('post', '/api/explain', async (request) => {
        const body = (await request.json()) as { customer_ids: string[] };
        await answered;
        return explanations(body.customer_ids);
      }),
    );
    renderAt(<App />, '/?tab=queue');
    await loaded();
    const explains = () => seen.filter((url) => url.pathname === '/api/explain');
    await waitFor(() => expect(explains()).toHaveLength(1));
    // To another page while the explanation is unanswered, and Back.
    await userEvent.click(
      screen.getAllByRole('link', { name: /^Model performance/ })[0] as HTMLElement,
    );
    await waitFor(() => expect(window.location.pathname).toBe('/performance'));
    act(() => window.history.back());
    await loaded();
    expect(tab(/^Retention queue/)).toHaveAttribute('aria-selected', 'true');
    act(() => release());
    const queue = await screen.findByRole('table', { name: 'Flagged customers' });
    expect(await within(queue).findAllByText('Tenure +31.7')).not.toHaveLength(0);
    await settle();
    expect(explains()).toHaveLength(1);
  });

  it('keeps the queue as the reader left it when they come back from another tab', async () => {
    showOverview('/?tab=queue');
    await loaded();
    const queue = await screen.findByRole('table', { name: 'Flagged customers' });
    const first = QUEUE_ROWS[0]?.id ?? '';
    await userEvent.click(within(queue).getByRole('checkbox', { name: `Select ${first}` }));
    expect(screen.getByRole('region', { name: 'Bulk actions' })).toHaveTextContent('1 selected');
    await userEvent.click(tab(/^Summary/));
    await userEvent.click(tab(/^Retention queue/));
    expect(within(queue).getByRole('checkbox', { name: `Select ${first}` })).toBeChecked();
    expect(screen.getByRole('region', { name: 'Bulk actions' })).toHaveTextContent('1 selected');
  });

  it('asks for nobody when the threshold flags nobody', async () => {
    const seen = recordRequests();
    const book = overview();
    showOverview('/', { ...book, bands: book.bands.map((band) => ({ ...band, flagged: 0 })) });
    await loaded();
    expect(within(region(/^Customers to call first/)).getByText('Nobody is flagged')).toBeVisible();
    expect(seen.filter((url) => url.pathname === '/api/customers')).toEqual([]);
    expect(screen.queryByRole('link', { name: 'View explanations in Retention Queue' })).toBeNull();
  });

  it('shows the customers whatever the explainer would say, since it is never asked', async () => {
    const seen = recordRequests();
    showOverview();
    server.use(refuse('post', '/api/explain', 503, 'explanations_busy'));
    await loaded();
    const card = region(/^Customers to call first/);
    expect(await within(card).findAllByRole('link', { name: /^DEMO-/ })).toHaveLength(3);
    await settle();
    expect(within(card).queryByText('Attributions are unavailable right now.')).toBeNull();
    expect(seen.filter((url) => url.pathname === '/api/explain')).toEqual([]);
  });
});

describe('Overview summary: segments to watch', () => {
  it('shows the API’s ranked groups in its order, the first four, with its lift and size', async () => {
    const seen = recordRequests();
    const ranked = [
      reported('Month-to-month', '0-3', 400, 240),
      reported('Month-to-month', '4-72', 1040, 280),
      reported('One year', '0-3', 100, 20),
      reported('One year', '4-72', 988, 148),
      reported('Two year', '4-72', 1000, 50),
    ];
    server.use(...overviewApi());
    // Added after, so it answers first.
    server.use(answer('get', '/api/segments', contractByTenure({ ranked })));
    renderAt(<App />, '/');
    const card = await screen.findByRole('region', { name: 'Segments to watch' });
    const items = await within(card).findAllByRole('listitem');
    expect(items).toHaveLength(4);
    expect(items[0]).toHaveTextContent(
      'Contract Month-to-month, tenure 0–3 months60.0%2.4× avg · n 400',
    );
    expect(items[3]).toHaveTextContent('Contract One year, tenure 4–72 months');
    expect(card).toHaveTextContent('TRAIN · 4,000');
    expect(card).toHaveTextContent('20 or more customers each');
    const asked = seen.find((url) => url.pathname === '/api/segments');
    expect(asked?.searchParams.get('rows')).toBe('Contract');
    expect(asked?.searchParams.get('cols')).toBe('tenure');
    expect(within(card).getByRole('link', { name: /All patterns/ })).toHaveAttribute(
      'href',
      '/patterns',
    );
  });

  it('asks for no segments while the overview already says outcomes are withheld', async () => {
    const seen = recordRequests();
    showOverview('/', overview({ withheld_reason: 'the split is unverified.' }));
    await loaded();
    expect(region('Segments to watch')).toHaveTextContent(
      'Observed outcomes are withheld: the split is unverified.',
    );
    expect(seen.filter((url) => url.pathname === '/api/segments')).toEqual([]);
  });

  it('says outcomes are withheld when the API withholds them', async () => {
    server.use(...overviewApi());
    server.use(refuse('get', '/api/segments', 409, 'outcomes_withheld'));
    renderAt(<App />, '/');
    await loaded();
    expect(
      await within(region('Segments to watch')).findByText('Observed outcomes are withheld.'),
    ).toBeInTheDocument();
  });
});

describe('Overview summary: churn by tenure', () => {
  it('names the card by what its figures show, and breaks the line at a hidden bucket', async () => {
    const book = overview();
    const lifecycle = book.lifecycle;
    if (!lifecycle) throw new Error('the fixture has a lifecycle');
    showOverview('/', {
      ...book,
      lifecycle: {
        ...lifecycle,
        buckets: [
          reported('0-3', null, 640, 360),
          hidden('4-6', null, 12),
          reported('7-12', null, 560, 400),
        ],
      },
    });
    await loaded();
    const card = region('Churn by tenure');
    expect(card).toHaveTextContent('hidden');
    // Two dots, no area under a broken line.
    expect(card.querySelectorAll('circle')).toHaveLength(2);
    expect(card.querySelector('path[fill^="url("]')).toBeNull();
    const path = card.querySelector('path')?.getAttribute('d') ?? '';
    expect(path.match(/M/g)).toHaveLength(2);
  });
});

describe('Overview summary: quick actions', () => {
  it('leads to the pages that do each thing', async () => {
    showOverview();
    await loaded();
    const links = within(region('Quick actions')).getAllByRole('link');
    expect(links.map((link) => [link.textContent, link.getAttribute('href')])).toEqual([
      ['Find a customer Search and filter every scored customer', '/explorer'],
      ['Plan a campaign Set assumptions and choose a cut-off', '/simulator'],
      ['Score a file Upload a CSV file to the server', '/batch'],
      ['Check the model Test results and calibration', '/performance'],
    ]);
  });
});

describe('Overview summary: wording', () => {
  it('uses no causal word anywhere on the summary', async () => {
    showOverview();
    await loaded();
    await within(region('Briefing')).findByText(/Expected calibration error 0\.031/);
    expect(document.getElementById('main')?.textContent ?? '').not.toMatch(CAUSAL);
  });
});
