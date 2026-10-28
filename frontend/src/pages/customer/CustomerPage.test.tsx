import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw/http';
import type { Schemas } from '../../api/client';
import { App } from '../../app/App';
import { navigate } from '../../app/router';
import { downloadText } from '../../lib/download';
import {
  customerApi,
  EXAMPLES,
  explained,
  explainResponse,
  LINKED,
  prediction,
  README_RECORD,
} from '../../test/customerApi';
import { modelInfo } from '../../test/fixtures';
import { entriesAdded } from '../../test/history';
import { press } from '../../test/keyboard';
import { renderAt } from '../../test/render';
import { answer, recordRequests, refuse, server } from '../../test/server';

vi.mock('../../lib/download', () => ({ downloadText: vi.fn(() => true) }));

/** The page at ``path``; ``before`` handlers answer ahead of the fixtures. */
function showCustomer(path = '/customer', before: Parameters<typeof server.use> = []) {
  server.use(...customerApi());
  if (before.length) server.use(...before);
  return renderAt(<App />, path);
}

/** Every POST body sent to ``path``, in order. */
function postsTo(path: string): unknown[] {
  const bodies: unknown[] = [];
  server.events.on('request:start', ({ request }) => {
    if (request.method === 'POST' && new URL(request.url).pathname === path)
      void request
        .clone()
        .json()
        .then((body) => bodies.push(body));
  });
  return bodies;
}

async function loaded() {
  return screen.findByRole('heading', { level: 1, name: 'What moved this estimate' });
}
const picker = () => screen.getByRole('navigation', { name: 'Customers' });
const card = (name: RegExp) => within(picker()).getByRole('link', { name });
const prediction_ = () => screen.getByRole('region', { name: 'Prediction' });
const waterfall = () => screen.getByRole('list', { name: 'Contributions' });
const rows = () => within(waterfall()).getAllByRole('listitem');
const FAILED: Schemas['ErrorResponse'] = {
  error: 'internal_error',
  detail: 'Words from the server that are never shown.',
  problems: [],
};

/** Released after every test, so a failed one never leaves a request waiting. */
const gates: (() => void)[] = [];
afterEach(() => {
  for (const open of gates.splice(0)) open();
});

/** Requests to ``path`` wait until ``release``, then the fixtures answer them. */
function hold(path: '/api/predict' | '/api/explain' | '/api/customers/:customer_id') {
  const held: Request[] = [];
  let release: () => void = () => undefined;
  const opened = new Promise<void>((resolve) => (release = resolve));
  gates.push(() => release());
  const [fixture] = customerApi().filter((h) => h.info.path === path);
  const method = path.startsWith('/api/customers') ? http.get : http.post;
  const handler = method(path, async (info) => {
    held.push(info.request);
    await opened;
    const answered = await fixture?.run({ request: info.request, requestId: info.requestId });
    return answered?.response ?? HttpResponse.json(FAILED, { status: 500 });
  });
  return { handler, held, release: () => release() };
}
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe('Single customer: the README example', () => {
  it('scores it through the API, then explains the same record', async () => {
    const seen = recordRequests();
    const predicted = postsTo('/api/predict');
    const explainedBodies = postsTo('/api/explain');
    showCustomer();
    await loaded();
    expect(
      await screen.findByText('API example: 80.3%, Critical band, flagged at 0.36.'),
    ).toBeInTheDocument();
    const card = prediction_();
    expect(within(card).getByText('80.3%')).toBeInTheDocument();
    expect(within(card).getByText('Critical band')).toBeInTheDocument();
    expect(within(card).getByText('Flagged at 0.36')).toBeInTheDocument();
    expect(
      within(card).getByText('0.443 above the threshold. This customer would receive an offer.'),
    ).toBeInTheDocument();
    const lit = card.querySelectorAll('[data-on="true"]');
    expect([...lit].map((band) => band.getAttribute('data-band'))).toEqual(['Critical']);
    expect(card.querySelectorAll('[data-on="false"]')).toHaveLength(3);
    await waitFor(() => expect(explainedBodies).toEqual([{ record: README_RECORD }]));
    expect(predicted).toEqual([README_RECORD]);
    // The attributions are asked for only once the probability is in.
    const paths = seen.map((url) => url.pathname);
    expect(paths.indexOf('/api/predict')).toBeLessThan(paths.indexOf('/api/explain'));
  });

  it('asks for no attribution before the API has scored the record', async () => {
    const seen = recordRequests();
    const predict = hold('/api/predict');
    showCustomer('/customer', [predict.handler]);
    await loaded();
    await pause(400);
    expect(seen.some((url) => url.pathname === '/api/predict')).toBe(true);
    expect(seen.some((url) => url.pathname === '/api/explain')).toBe(false);
    predict.release();
    await waitFor(() => expect(seen.some((url) => url.pathname === '/api/explain')).toBe(true));
  });

  it('colours an attribution that rounds to nothing as neither raising nor lowering', async () => {
    showCustomer('/customer', [
      http.post('/api/explain', async ({ request }) => {
        const body = (await request.json()) as Schemas['ExplainRequest'];
        const record = body.record ?? README_RECORD;
        const full = explained(record, null);
        const last = full.contributions.at(-1);
        if (last) last.shap_value = 0.0004;
        return HttpResponse.json(explainResponse([full]));
      }),
    ]);
    await loaded();
    await screen.findByRole('list', { name: 'Contributions' });
    await userEvent.click(screen.getByRole('button', { name: 'All 18' }));
    const phone = rows().at(-2);
    expect(phone).toHaveTextContent('Phone service · Yes0.0');
    expect(phone?.querySelector('[data-direction="none"]')).not.toBeNull();
    expect(phone?.querySelector('[data-direction="raises"]')).toBeNull();
  });

  it('draws the waterfall from the base, eight attributions, the rest and the served probability', async () => {
    showCustomer();
    await loaded();
    await screen.findByRole('list', { name: 'Contributions' });
    const items = rows();
    expect(items).toHaveLength(11);
    expect(items[0]).toHaveTextContent('Average customer base25.8%');
    expect(items[1]).toHaveTextContent('Tenure · 3 months+30.8');
    expect(items[2]).toHaveTextContent('Monthly charges · $95.00−16.1');
    expect(items[9]).toHaveTextContent('10 other inputs · combined+15.5');
    expect(items[10]).toHaveTextContent('This customer80.3%');
    const value = (item: HTMLElement | undefined) =>
      item?.querySelector('[data-direction]:last-child')?.getAttribute('data-direction');
    expect(value(items[1])).toBe('raises');
    expect(value(items[2])).toBe('lowers');
    // The artboard draws the threshold through the attributions, not the base.
    expect(items[0]?.querySelector('[class*="wfThreshold"]')).toBeNull();
    expect(items[1]?.querySelector('[class*="wfThreshold"]')).not.toBeNull();
    expect(
      screen.getByText(
        'Base plus contributions reproduces the served probability; the server checks that sum. Permutation explainer, 10 orderings, fixed seed, the same 100-customer training background on every page. Dashed line: threshold 0.36.',
      ),
    ).toBeInTheDocument();
  });

  it('shows five, eight or every attribution, counted from the data', async () => {
    showCustomer();
    await loaded();
    await screen.findByRole('list', { name: 'Contributions' });
    const shown = screen.getByRole('group', { name: 'Contributions shown' });
    expect(within(shown).getByRole('button', { name: 'Top 8' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await userEvent.click(within(shown).getByRole('button', { name: 'Top 5' }));
    expect(rows()).toHaveLength(8);
    expect(rows()[6]).toHaveTextContent('13 other inputs · combined');
    await userEvent.click(within(shown).getByRole('button', { name: 'All 18' }));
    expect(rows()).toHaveLength(20);
    expect(within(waterfall()).queryByText(/other input/)).not.toBeInTheDocument();
  });

  it('writes the four strongest as sentences, and highlights them in the profile', async () => {
    showCustomer();
    await loaded();
    const sentences = await screen.findByRole('region', { name: 'In plain sentences' });
    const items = await within(sentences).findAllByRole('listitem');
    expect(items.map((item) => item.textContent)).toEqual([
      'Tenure (3 months) raises the estimated risk by 30.8 pp, relative to an average customer.',
      'Monthly charges ($95.00) lowers the estimated risk by 16.1 pp, relative to an average customer.',
      'Internet service (Fiber optic) raises the estimated risk by 16.1 pp, relative to an average customer.',
      'Total charges ($285.00) lowers the estimated risk by 9.1 pp, relative to an average customer.',
    ]);
    const profile = screen.getByRole('region', { name: 'Profile' });
    expect(within(profile).getByText('18 model inputs')).toBeInTheDocument();
    const strong = within(profile).getAllByText('(among the four strongest)');
    expect(strong).toHaveLength(4);
    expect(within(profile).getByText('3 months').closest('dd')).toHaveAttribute(
      'data-strong',
      'true',
    );
    expect(within(profile).getByText('Month-to-month').closest('dd')).toHaveAttribute(
      'data-strong',
      'false',
    );
  });

  it('shows the API’s own response, and the address the browser used', async () => {
    showCustomer();
    await loaded();
    const panel = await screen.findByRole('region', { name: 'API response' });
    expect(await within(panel).findByText('200 OK')).toBeInTheDocument();
    expect(panel).toHaveTextContent('"churn_probability": 0.8029793735676088,');
    expect(panel).toHaveTextContent('"risk_band": "Critical",');
    expect(panel).toHaveTextContent('"flagged": true,');
    expect(panel).toHaveTextContent('"model_key": "logistic_regression",');
    // The last field closes the object: no comma after it.
    expect(panel.querySelector('pre')?.textContent).toMatch(/not a causal statement\."\n}$/);
    expect(
      within(panel).getByText(
        `POST /api/predict · ${window.location.host} · same number as the library`,
      ),
    ).toBeInTheDocument();
  });

  it('downloads the prediction the page shows, as the API gave it', async () => {
    vi.mocked(downloadText).mockClear();
    showCustomer();
    await loaded();
    const button = screen.getByRole('button', { name: 'Download prediction (CSV)' });
    await waitFor(() => expect(button).toHaveAttribute('aria-disabled', 'false'));
    await userEvent.click(button);
    const [name, text] = vi.mocked(downloadText).mock.calls[0] ?? [];
    expect(name).toBe('churnsense_single_prediction.csv');
    const [header, row] = (text ?? '').trimEnd().split('\n');
    expect(header?.split(',')).toEqual([
      ...Object.keys(README_RECORD),
      'churn_probability',
      'risk_band',
      'flagged',
    ]);
    expect(row?.split(',').slice(-3)).toEqual(['0.8029793735676088', 'Critical', 'true']);
    expect(row?.split(',')[Object.keys(README_RECORD).indexOf('tenure')]).toBe('3');
  });

  it('says so when the browser refuses to save the file, and does nothing while there is nothing to save', async () => {
    vi.mocked(downloadText).mockClear();
    vi.mocked(downloadText).mockReturnValueOnce(false);
    showCustomer('/customer', [
      http.post('/api/predict', () => HttpResponse.json(FAILED, { status: 500 })),
    ]);
    await loaded();
    const button = screen.getByRole('button', { name: 'Download prediction (CSV)' });
    await within(prediction_()).findByText('The prediction could not be loaded.');
    await userEvent.click(button);
    expect(downloadText).not.toHaveBeenCalled();
    server.use(...customerApi());
    await userEvent.click(within(prediction_()).getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(button).toHaveAttribute('aria-disabled', 'false'));
    await userEvent.click(button);
    expect(await screen.findByText('The browser did not save the file.')).toBeInTheDocument();
    await userEvent.click(button);
    expect(screen.queryByText('The browser did not save the file.')).not.toBeInTheDocument();
  });

  it('announces every refusal to save, and forgets it once another customer is shown', async () => {
    vi.mocked(downloadText).mockClear();
    vi.mocked(downloadText).mockReturnValue(false);
    try {
      showCustomer();
      await loaded();
      const button = screen.getByRole('button', { name: 'Download prediction (CSV)' });
      await waitFor(() => expect(button).toHaveAttribute('aria-disabled', 'false'));
      await userEvent.click(button);
      const status = await screen.findByText('The browser did not save the file.');
      // A second refusal empties the status first, so it is read out again.
      const said: string[] = [];
      const observer = new MutationObserver(() => said.push(status.textContent));
      observer.observe(status, { childList: true, characterData: true, subtree: true });
      await userEvent.click(button);
      await waitFor(() => expect(said).toEqual(['', 'The browser did not save the file.']));
      observer.disconnect();
      await userEvent.click(card(/^DEMO-0005/));
      await screen.findByText(/^DEMO-0005: /);
      expect(screen.queryByText('The browser did not save the file.')).not.toBeInTheDocument();
      // Forgotten, not hidden: coming back does not say it again.
      await userEvent.click(card(/^API example/));
      await screen.findByText('API example: 80.3%, Critical band, flagged at 0.36.');
      await pause(200);
      expect(screen.queryByText('The browser did not save the file.')).not.toBeInTheDocument();
      // A new customer's refusal goes with the first edit: the figures it was about are gone.
      await userEvent.click(screen.getByRole('button', { name: 'Score a new customer' }));
      await screen.findByText('New customer: 80.3%, Critical band, flagged at 0.36.');
      await userEvent.click(button);
      await screen.findByText('The browser did not save the file.');
      await userEvent.type(screen.getByRole('textbox', { name: 'Tenure (months)' }), '0');
      expect(screen.queryByText('The browser did not save the file.')).not.toBeInTheDocument();
    } finally {
      vi.mocked(downloadText).mockReturnValue(true);
    }
  });

  it('keeps the reader’s choice of every attribution while the next customer’s load', async () => {
    showCustomer();
    await loaded();
    await screen.findByRole('list', { name: 'Contributions' });
    await userEvent.click(screen.getByRole('button', { name: 'All 18' }));
    const explain = hold('/api/explain');
    server.use(explain.handler);
    await userEvent.click(card(/^DEMO-0005/));
    await waitFor(() => expect(explain.held).toHaveLength(1));
    expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true');
    explain.release();
    expect(await screen.findByRole('button', { name: 'All 18' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('takes the threshold it draws from the prediction the API served', async () => {
    showCustomer('/customer', [
      answer('get', '/api/model-info', { ...modelInfo(), threshold: 0.36 }),
      http.post('/api/predict', async ({ request }) =>
        HttpResponse.json(prediction((await request.json()) as typeof README_RECORD, 0.4)),
      ),
    ]);
    await loaded();
    const card = prediction_();
    expect(await within(card).findByText('Flagged at 0.40')).toBeInTheDocument();
    expect(within(card).getByText('threshold 0.40')).toBeInTheDocument();
    expect(
      within(card).getByText('0.403 above the threshold. This customer would receive an offer.'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('API example: 80.3%, Critical band, flagged at 0.40.'),
    ).toBeInTheDocument();
    expect(await screen.findByText(/Dashed line: threshold 0\.40\.$/)).toBeInTheDocument();
  });

  it('counts the attributions it is given, never a typed 18', async () => {
    showCustomer('/customer', [
      http.post('/api/explain', async ({ request }) => {
        const body = (await request.json()) as Schemas['ExplainRequest'];
        const full = explained(body.record ?? README_RECORD, null);
        return HttpResponse.json(
          explainResponse([{ ...full, contributions: full.contributions.slice(0, 17) }]),
        );
      }),
    ]);
    await loaded();
    await screen.findByRole('list', { name: 'Contributions' });
    expect(rows()[9]).toHaveTextContent('9 other inputs · combined');
    expect(screen.getByRole('button', { name: 'All 17' })).toBeInTheDocument();
  });

  it('says why nothing follows when the API could not score it, and asks for nothing more', async () => {
    const seen = recordRequests();
    showCustomer('/customer', [
      http.post('/api/predict', () => HttpResponse.json(FAILED, { status: 500 })),
    ]);
    await loaded();
    expect(
      await within(prediction_()).findByText('The prediction could not be loaded.'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('The attributions follow once the inputs can be scored.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Computing attributions')).not.toBeInTheDocument();
    const panel = screen.getByRole('region', { name: 'API response' });
    expect(within(panel).getByText('No response for these inputs.')).toBeInTheDocument();
    expect(within(panel).queryByText('Waiting for the API')).not.toBeInTheDocument();
    expect(seen.some((url) => url.pathname === '/api/explain')).toBe(false);
    // Its card says so too, on every view, rather than looking as if it were loading.
    expect(card(/^API example/)).toHaveAccessibleName('API example … … not loaded');
    // Asking again keeps the reader's place: the focus goes to the prediction.
    server.use(...customerApi());
    await press(within(prediction_()).getByRole('button', { name: 'Try again' }));
    expect(prediction_()).toHaveFocus();
    expect(await within(prediction_()).findByText('80.3%')).toBeInTheDocument();
  });
});

describe('Single customer: the picker', () => {
  it('lists the README example, the four examples and the new customer, each with its address', async () => {
    showCustomer();
    await loaded();
    expect(await within(picker()).findByRole('link', { name: /^DEMO-0005/ })).toBeInTheDocument();
    const links = within(picker()).getAllByRole('link');
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/customer',
      '/customer?id=DEMO-0005',
      '/customer?id=DEMO-0006',
      '/customer?id=DEMO-0007',
      '/customer?id=DEMO-0008',
      '/customer?live=true',
    ]);
    expect(card(/^API example/)).toHaveAttribute('aria-current', 'page');
    expect(card(/^DEMO-0005/)).not.toHaveAttribute('aria-current');
    expect(card(/^DEMO-0005/)).toHaveAccessibleName('DEMO-0005 3.0% Low validation · stayed');
    expect(card(/^DEMO-0007/)).toHaveAccessibleName(
      'DEMO-0007 41.9% Moderate validation · churned',
    );
    await waitFor(() =>
      expect(card(/^API example/)).toHaveAccessibleName(
        'API example 80.3% Critical README example',
      ),
    );
    expect(card(/^New customer/)).toHaveAccessibleName(
      'New customer live Edit any input scored by the API',
    );
  });

  it('opens an example from its own figures, in one history entry, and Back returns', async () => {
    const seen = recordRequests();
    const explainedBodies = postsTo('/api/explain');
    showCustomer();
    await loaded();
    await screen.findByText('API example: 80.3%, Critical band, flagged at 0.36.');
    const added = entriesAdded();
    await userEvent.click(await within(picker()).findByRole('link', { name: /^DEMO-0007/ }));
    expect(window.location.search).toBe('?id=DEMO-0007');
    expect(added()).toBe(1);
    expect(
      await screen.findByText('DEMO-0007: 41.9%, Moderate band, flagged at 0.36.'),
    ).toBeInTheDocument();
    expect(card(/^DEMO-0007/)).toHaveAttribute('aria-current', 'page');
    // The example's detail came with the examples: it is not asked for again.
    expect(seen.some((url) => url.pathname === '/api/customers/DEMO-0007')).toBe(false);
    await waitFor(() => expect(explainedBodies).toContainEqual({ customer_ids: ['DEMO-0007'] }));
    act(() => window.history.back());
    await waitFor(() => expect(window.location.search).toBe(''));
    expect(
      await screen.findByText('API example: 80.3%, Critical band, flagged at 0.36.'),
    ).toBeInTheDocument();
  });

  it('opens an example from a link without asking for it, in its own place', async () => {
    const seen = recordRequests();
    showCustomer('/customer?id=DEMO-0005');
    await loaded();
    expect(
      await screen.findByText('DEMO-0005: 3.0%, Low band, not flagged at 0.36.'),
    ).toBeInTheDocument();
    const links = within(picker()).getAllByRole('link');
    expect(links[0]).toHaveAccessibleName(/^API example/);
    expect(links[1]).toHaveAttribute('aria-current', 'page');
    expect(seen.some((url) => url.pathname === '/api/customers/DEMO-0005')).toBe(false);
  });

  it('puts a customer a link opened first, and keeps it there after another is chosen', async () => {
    showCustomer('/customer?id=DEMO-0042');
    await loaded();
    expect(
      await screen.findByText('DEMO-0042: 55.0%, High band, flagged at 0.36.'),
    ).toBeInTheDocument();
    await within(picker()).findByRole('link', { name: /^DEMO-0005/ });
    const links = within(picker()).getAllByRole('link');
    expect(links[0]).toHaveAccessibleName('DEMO-0042 55.0% High test · withheld');
    expect(links[0]).toHaveAttribute('aria-current', 'page');
    await userEvent.click(card(/^DEMO-0005/));
    expect(within(picker()).getAllByRole('link')[0]).toHaveAccessibleName(
      'DEMO-0042 55.0% High test · withheld',
    );
  });

  it('keeps the focus on the card that was followed', async () => {
    showCustomer();
    await loaded();
    const link = await within(picker()).findByRole('link', { name: /^DEMO-0006/ });
    act(() => link.focus());
    await userEvent.keyboard('{Enter}');
    expect(window.location.search).toBe('?id=DEMO-0006');
    expect(card(/^DEMO-0006/)).toHaveFocus();
  });
});

describe('Single customer: a customer in the book', () => {
  it('shows the served figures, explains it by ID and shows the API’s answer for its inputs', async () => {
    const predicted = postsTo('/api/predict');
    const explainedBodies = postsTo('/api/explain');
    showCustomer('/customer?id=DEMO-0042');
    await loaded();
    const card = prediction_();
    expect(await within(card).findByText('55.0%')).toBeInTheDocument();
    expect(within(card).getByText('High band')).toBeInTheDocument();
    await waitFor(() => expect(explainedBodies).toEqual([{ customer_ids: ['DEMO-0042'] }]));
    await waitFor(() => expect(predicted).toContainEqual(LINKED.features));
    const panel = screen.getByRole('region', { name: 'API response' });
    expect(await within(panel).findByText('200 OK')).toBeInTheDocument();
    expect(panel).toHaveTextContent('"churn_probability": 0.55,');
  });

  it('never shows one customer’s API answer under the next while it loads', async () => {
    showCustomer('/customer?id=DEMO-0042');
    await loaded();
    const panel = screen.getByRole('region', { name: 'API response' });
    await waitFor(() => expect(panel).toHaveTextContent('"churn_probability": 0.55,'));
    const predict = hold('/api/predict');
    server.use(predict.handler);
    await userEvent.click(await within(picker()).findByRole('link', { name: /^DEMO-0005/ }));
    expect(await within(prediction_()).findByText('3.0%')).toBeInTheDocument();
    const shown = screen.getByRole('region', { name: 'API response' });
    expect(shown).not.toHaveTextContent('0.55');
    expect(within(shown).queryByText('200 OK')).not.toBeInTheDocument();
    predict.release();
    await waitFor(() => expect(shown).toHaveTextContent('"churn_probability": 0.030126,'));
  });

  it('shows nothing of the last customer while a linked one loads', async () => {
    showCustomer();
    await loaded();
    await screen.findByText('API example: 80.3%, Critical band, flagged at 0.36.');
    await screen.findByRole('list', { name: 'Contributions' });
    const detail = hold('/api/customers/:customer_id');
    server.use(detail.handler);
    act(() => navigate('/customer?id=DEMO-0042'));
    await waitFor(() => expect(detail.held).toHaveLength(1));
    expect(within(prediction_()).queryByText('80.3%')).not.toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Contributions' })).not.toBeInTheDocument();
    const panel = screen.getByRole('region', { name: 'API response' });
    expect(within(panel).queryByText('200 OK')).not.toBeInTheDocument();
    expect(screen.queryByText(/^API example: /)).not.toBeInTheDocument();
    detail.release();
    expect(await within(prediction_()).findByText('55.0%')).toBeInTheDocument();
  });

  it('downloads a book customer with its ID first', async () => {
    vi.mocked(downloadText).mockClear();
    showCustomer('/customer?id=DEMO-0042');
    await loaded();
    const button = screen.getByRole('button', { name: 'Download prediction (CSV)' });
    await waitFor(() => expect(button).toHaveAttribute('aria-disabled', 'false'));
    await userEvent.click(button);
    const [header, row] = (vi.mocked(downloadText).mock.calls[0]?.[1] ?? '').split('\n');
    expect(header?.split(',')[0]).toBe('customerID');
    expect(row?.split(',')[0]).toBe('DEMO-0042');
    expect(row?.split(',').slice(-3)).toEqual(['0.55', 'High', 'true']);
  });

  it('says so when the customer is not in the book, and asks for no explanation', async () => {
    const seen = recordRequests();
    showCustomer('/customer?id=DEMO-0099');
    await loaded();
    // Shown, and announced.
    expect(await screen.findAllByText('There is no customer DEMO-0099 in the book.')).toHaveLength(
      2,
    );
    expect(seen.some((url) => url.pathname === '/api/explain')).toBe(false);
    expect(screen.getByRole('button', { name: 'Download prediction (CSV)' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(
      screen
        .getAllByRole('status')
        .some((s) => s.textContent === 'There is no customer DEMO-0099 in the book.'),
    ).toBe(true);
    expect(card(/^DEMO-0099/)).toHaveAccessibleName(/not in the book$/);
    await userEvent.click(card(/^DEMO-0005/));
    expect(within(picker()).queryByRole('link', { name: /^DEMO-0099/ })).not.toBeInTheDocument();
  });

  it('asks for nothing with an ID the API would refuse, and shows the README example', async () => {
    const seen = recordRequests();
    showCustomer('/customer?id=..');
    await loaded();
    expect(
      await screen.findByText('API example: 80.3%, Critical band, flagged at 0.36.'),
    ).toBeInTheDocument();
    const asked = new Set(seen.map((url) => url.pathname));
    expect([...asked].sort()).toEqual([
      '/api/customers/examples',
      '/api/explain',
      '/api/health',
      '/api/model-info',
      '/api/predict',
    ]);
  });

  it('offers a retry when the customer could not be loaded, never the server’s words', async () => {
    showCustomer('/customer?id=DEMO-0042', [
      http.get('/api/customers/DEMO-0042', () => HttpResponse.json(FAILED, { status: 500 })),
    ]);
    await loaded();
    expect(await screen.findByText('This customer could not be loaded.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    expect(screen.queryByText(/never shown/)).not.toBeInTheDocument();
    server.use(...customerApi());
    await press(screen.getByRole('button', { name: 'Try again' }));
    expect(document.getElementById('main')).toHaveFocus();
    expect(await within(prediction_()).findByText('55.0%')).toBeInTheDocument();
  });
});

describe('Single customer: the attributions', () => {
  it('asks once more after a rate limit, and draws what the second answer gives', async () => {
    let calls = 0;
    showCustomer('/customer', [
      http.post('/api/explain', async ({ request }) => {
        calls += 1;
        if (calls === 1)
          return HttpResponse.json(
            { error: 'rate_limited', detail: 'x', problems: [] },
            { status: 429, headers: { 'Retry-After': '1' } },
          );
        const body = (await request.json()) as Schemas['ExplainRequest'];
        return HttpResponse.json(explainResponse([explained(body.record ?? README_RECORD, null)]));
      }),
    ]);
    await loaded();
    expect(
      await screen.findByRole('list', { name: 'Contributions' }, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(calls).toBe(2);
    expect(screen.queryByText('Attributions are unavailable right now.')).not.toBeInTheDocument();
  });

  it('says when they are unavailable, and asks again on request', async () => {
    showCustomer('/customer?id=DEMO-0042', [refuse('post', '/api/explain', 429, 'rate_limited')]);
    await loaded();
    // Asked once more by itself first, after the server's wait.
    expect(
      await screen.findByText('Attributions are unavailable right now.', {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    server.use(...customerApi());
    await press(screen.getByRole('button', { name: 'Ask for the attributions again' }));
    expect(screen.getByRole('heading', { name: 'Contribution waterfall' })).toHaveFocus();
    expect(await screen.findByRole('list', { name: 'Contributions' })).toBeInTheDocument();
  });

  it('says plainly when they can never be computed, with nothing to retry', async () => {
    showCustomer('/customer', [refuse('post', '/api/explain', 503, 'explanations_unavailable')]);
    await loaded();
    expect(
      await screen.findByText(
        'Attributions cannot be computed without the verified training background.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Ask for the attributions again' }),
    ).not.toBeInTheDocument();
    // The prediction stands without them.
    expect(within(prediction_()).getByText('80.3%')).toBeInTheDocument();
  });

  it('asks no more once the server says they can never be computed', async () => {
    const seen = recordRequests();
    const asked = () => seen.filter((url) => url.pathname === '/api/explain').length;
    showCustomer('/customer', [refuse('post', '/api/explain', 503, 'explanations_unavailable')]);
    await loaded();
    const never = 'Attributions cannot be computed without the verified training background.';
    expect(await screen.findByText(never)).toBeInTheDocument();
    expect(asked()).toBe(1);
    await userEvent.click(card(/^DEMO-0005/));
    await screen.findByText(/^DEMO-0005: /);
    await userEvent.click(screen.getByRole('button', { name: 'Score a new customer' }));
    const tenure = screen.getByRole('textbox', { name: 'Tenure (months)' });
    await userEvent.clear(tenure);
    await userEvent.type(tenure, '40');
    await screen.findByText('New customer: 50.0%, High band, flagged at 0.36.');
    await pause(700);
    await userEvent.click(card(/^API example/));
    await screen.findByText('API example: 80.3%, Critical band, flagged at 0.36.');
    expect(asked()).toBe(1);
    expect(screen.getByText(never)).toBeInTheDocument();
    expect(screen.queryByText('Computing attributions')).not.toBeInTheDocument();
  });
});

describe('Single customer: a new customer', () => {
  async function scoreNew() {
    showCustomer();
    await loaded();
    await screen.findByText('API example: 80.3%, Critical band, flagged at 0.36.');
    await userEvent.click(screen.getByRole('button', { name: 'Score a new customer' }));
  }
  const form = () => screen.getByRole('region', { name: 'New customer' });

  it('opens on the README example, ready to type, without asking the API again', async () => {
    const predicted = postsTo('/api/predict');
    await scoreNew();
    expect(window.location.search).toBe('?live=true');
    expect(screen.getByRole('textbox', { name: 'Tenure (months)' })).toHaveFocus();
    expect(screen.getByRole('textbox', { name: 'Tenure (months)' })).toHaveValue('3');
    expect(screen.getByRole('textbox', { name: 'Monthly charges ($)' })).toHaveValue('95');
    expect(within(form()).getByText('scored live')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Senior' })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Streaming TV' })).toBeChecked();
    expect(
      await screen.findByText('New customer: 80.3%, Critical band, flagged at 0.36.'),
    ).toBeInTheDocument();
    expect(card(/^New customer/)).toHaveAccessibleName(
      'New customer 80.3% Critical scored by the API',
    );
    const current = within(picker()).getAllByRole('link', { current: 'page' });
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveAccessibleName(/^New customer/);
    expect(predicted).toEqual([README_RECORD]);
  });

  it('scores what is typed once typing stops, then explains it', async () => {
    const predicted = postsTo('/api/predict');
    const explainedBodies = postsTo('/api/explain');
    await scoreNew();
    const tenure = screen.getByRole('textbox', { name: 'Tenure (months)' });
    await userEvent.clear(tenure);
    await userEvent.type(tenure, '40');
    expect(
      await screen.findByText('New customer: 50.0%, High band, flagged at 0.36.'),
    ).toBeInTheDocument();
    const typed = { ...README_RECORD, tenure: 40 };
    // "4" on the way to "40" never reached the API.
    expect(predicted).toEqual([README_RECORD, typed]);
    await waitFor(() => expect(explainedBodies).toContainEqual({ record: typed }));
    expect(explainedBodies).not.toContainEqual({ record: { ...README_RECORD, tenure: 4 } });
  });

  it('keeps the last figures, dimmed and busy, until the new ones are in, and explains only what was scored', async () => {
    vi.mocked(downloadText).mockClear();
    const seen = recordRequests();
    await scoreNew();
    await screen.findByText('New customer: 80.3%, Critical band, flagged at 0.36.');
    await screen.findByRole('list', { name: 'Contributions' });
    const predict = hold('/api/predict');
    const explain = hold('/api/explain');
    server.use(predict.handler, explain.handler);
    const tenure = screen.getByRole('textbox', { name: 'Tenure (months)' });
    await userEvent.clear(tenure);
    await userEvent.type(tenure, '40');
    await waitFor(() =>
      expect(seen.filter((url) => url.pathname === '/api/predict')).toHaveLength(2),
    );
    // The old figures stay, marked as old; nothing is announced, exported or shown as the API's.
    expect(prediction_()).toHaveAttribute('aria-busy', 'true');
    expect(within(prediction_()).getByText('80.3%')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download prediction (CSV)' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(screen.queryByText(/^New customer: /)).not.toBeInTheDocument();
    const panel = screen.getByRole('region', { name: 'API response' });
    expect(within(panel).queryByText('200 OK')).not.toBeInTheDocument();
    expect(waterfall()).toHaveAttribute('aria-busy', 'true');
    // No attribution is asked for a record the API has not scored yet.
    await pause(700);
    expect(seen.filter((url) => url.pathname === '/api/explain')).toHaveLength(1);

    predict.release();
    expect(
      await screen.findByText('New customer: 50.0%, High band, flagged at 0.36.'),
    ).toBeInTheDocument();
    expect(prediction_()).toHaveAttribute('aria-busy', 'false');
    await waitFor(() =>
      expect(seen.filter((url) => url.pathname === '/api/explain')).toHaveLength(2),
    );
    // The attributions are still the last record's: dimmed, and their bar not in the new band.
    expect(waterfall()).toHaveAttribute('aria-busy', 'true');
    expect(rows().at(-1)?.querySelector('[data-direction="total"]')).not.toHaveAttribute(
      'data-band',
    );
    explain.release();
    await waitFor(() => expect(waterfall()).toHaveAttribute('aria-busy', 'false'));
    expect(rows().at(-1)).toHaveTextContent('This customer50.0%');
    expect(rows().at(-1)?.querySelector('[data-direction="total"]')).toHaveAttribute(
      'data-band',
      'High',
    );
  });

  it('never explains a record replaced before the reader paused', async () => {
    const explainedBodies = postsTo('/api/explain');
    await scoreNew();
    await screen.findByRole('list', { name: 'Contributions' });
    const typist = userEvent.setup({ delay: 300 });
    const tenure = screen.getByRole('textbox', { name: 'Tenure (months)' });
    await typist.clear(tenure);
    await typist.type(tenure, '40');
    await screen.findByText('New customer: 50.0%, High band, flagged at 0.36.');
    await waitFor(() =>
      expect(explainedBodies).toContainEqual({ record: { ...README_RECORD, tenure: 40 } }),
    );
    expect(explainedBodies).not.toContainEqual({ record: { ...README_RECORD, tenure: 4 } });
  });

  it('stops asking about inputs replaced while the API was answering', async () => {
    await scoreNew();
    await screen.findByRole('list', { name: 'Contributions' });
    const predict = hold('/api/predict');
    server.use(predict.handler);
    const tenure = screen.getByRole('textbox', { name: 'Tenure (months)' });
    await userEvent.clear(tenure);
    await userEvent.type(tenure, '10');
    await waitFor(() => expect(predict.held).toHaveLength(1));
    // "101": still within the API's bounds, so it is asked for and the "10" is dropped.
    await userEvent.type(tenure, '1');
    await waitFor(() => expect(predict.held).toHaveLength(2));
    expect(predict.held[0]?.signal.aborted).toBe(true);
    expect(predict.held[1]?.signal.aborted).toBe(false);
  });

  it('never explains a record the API refused to score', async () => {
    const explainedBodies = postsTo('/api/explain');
    await scoreNew();
    server.use(
      http.post('/api/predict', () =>
        HttpResponse.json(
          { error: 'validation_error', detail: 'x', problems: ['tenure: too large'] },
          { status: 422 },
        ),
      ),
    );
    const tenure = screen.getByRole('textbox', { name: 'Tenure (months)' });
    await userEvent.clear(tenure);
    await userEvent.type(tenure, '50');
    expect(await screen.findByText('tenure: too large')).toBeInTheDocument();
    expect(
      screen.getByText('The attributions follow once the inputs can be scored.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Contributions' })).not.toBeInTheDocument();
    expect(card(/^New customer/)).toHaveAccessibleName(/^New customer live/);
    await pause(700);
    expect(explainedBodies).not.toContainEqual({ record: { ...README_RECORD, tenure: 50 } });
  });

  it('sends a senior as "1"', async () => {
    const predicted = postsTo('/api/predict');
    await scoreNew();
    await userEvent.click(screen.getByRole('checkbox', { name: 'Senior' }));
    await waitFor(() => expect(predicted.at(-1)).toMatchObject({ SeniorCitizen: '1' }));
  });

  it('keeps the add-ons and phone lines consistent with the services chosen', async () => {
    const predicted = postsTo('/api/predict');
    await scoreNew();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Internet service' }), 'No');
    const security = screen.getByRole('checkbox', { name: 'Online security' });
    expect(security).toBeDisabled();
    expect(security).not.toBeChecked();
    await userEvent.click(screen.getByRole('checkbox', { name: 'Phone service' }));
    expect(screen.getByRole('combobox', { name: 'Multiple lines' })).toHaveValue(
      'No phone service',
    );
    await waitFor(() =>
      expect(predicted.at(-1)).toMatchObject({
        InternetService: 'No',
        OnlineSecurity: 'No internet service',
        StreamingTV: 'No internet service',
        PhoneService: 'No',
        MultipleLines: 'No phone service',
      }),
    );
  });

  it('offers only the choices the phone service allows, and says why total charges are fixed at no tenure', async () => {
    await scoreNew();
    const lines = screen.getByRole('combobox', { name: 'Multiple lines' });
    expect(lines).toBeEnabled();
    expect(
      within(lines)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['No', 'Yes']);
    await userEvent.click(screen.getByRole('checkbox', { name: 'Phone service' }));
    expect(lines).toBeDisabled();
    expect(lines).toHaveValue('No phone service');
    const tenure = screen.getByRole('textbox', { name: 'Tenure (months)' });
    await userEvent.clear(tenure);
    await userEvent.type(tenure, '0');
    const total = screen.getByRole('textbox', { name: 'Total charges ($)' });
    expect(total).toHaveAttribute('readonly');
    expect(total).toHaveAccessibleDescription('Nothing is billed before the first month.');
  });

  it('fills total charges from tenure and monthly charges, and resets to the README example', async () => {
    await scoreNew();
    const tenure = screen.getByRole('textbox', { name: 'Tenure (months)' });
    await userEvent.clear(tenure);
    await userEvent.type(tenure, '12');
    await userEvent.click(
      screen.getByRole('button', { name: 'Set total charges to tenure times monthly charges' }),
    );
    expect(screen.getByRole('textbox', { name: 'Total charges ($)' })).toHaveValue('1140');
    await userEvent.clear(tenure);
    await userEvent.type(tenure, '0');
    expect(screen.getByRole('textbox', { name: 'Total charges ($)' })).toHaveValue('0');
    await userEvent.click(screen.getByRole('button', { name: 'Reset to the README example' }));
    expect(tenure).toHaveValue('3');
    expect(screen.getByRole('textbox', { name: 'Total charges ($)' })).toHaveValue('285');
  });

  it('scores nothing while an amount cannot be sent, and says why', async () => {
    const predicted = postsTo('/api/predict');
    await scoreNew();
    await screen.findByText('New customer: 80.3%, Critical band, flagged at 0.36.');
    const total = screen.getByRole('textbox', { name: 'Total charges ($)' });
    // The live region is there, empty, before anything is wrong, so its first message is read.
    const live = form().querySelector('[aria-live="polite"]');
    expect(live).not.toBeNull();
    expect(live).toHaveTextContent('');
    await userEvent.clear(total);
    await userEvent.type(total, '0');
    expect(total).toHaveAttribute('aria-invalid', 'true');
    expect(total).toHaveAccessibleDescription(
      'Total charges are zero only for a customer with no tenure.',
    );
    expect(live).toHaveTextContent('Total charges are zero only for a customer with no tenure.');
    expect(
      screen.getByText('Total charges are zero only for a customer with no tenure.'),
    ).toBeInTheDocument();
    expect(within(prediction_()).getByText('—')).toBeInTheDocument();
    expect(within(prediction_()).queryByText('80.3%')).not.toBeInTheDocument();
    expect(
      screen.getByText('The attributions follow once the inputs can be scored.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Contributions' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download prediction (CSV)' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await pause(600);
    expect(predicted).toEqual([README_RECORD]);
    await userEvent.type(total, '5');
    expect(
      await screen.findByText('New customer: 87.0%, Critical band, flagged at 0.36.'),
    ).toBeInTheDocument();
  });

  it('shows what the API found wrong with the inputs, as text', async () => {
    await scoreNew();
    server.use(
      http.post('/api/predict', () =>
        HttpResponse.json(
          {
            error: 'validation_error',
            detail: 'x',
            problems: ['tenure: refused by the server'],
          },
          { status: 422 },
        ),
      ),
    );
    const tenure = screen.getByRole('textbox', { name: 'Tenure (months)' });
    await userEvent.clear(tenure);
    await userEvent.type(tenure, '50');
    expect(await screen.findByText('tenure: refused by the server')).toBeInTheDocument();
    expect(within(prediction_()).queryByText('80.3%')).not.toBeInTheDocument();
    // No answer is coming for these inputs, so the panel does not wait for one.
    const panel = screen.getByRole('region', { name: 'API response' });
    expect(within(panel).getByText('No response for these inputs.')).toBeInTheDocument();
  });

  it('keeps the figures steady when the API’s bounds arrive after them', async () => {
    let release: () => void = () => undefined;
    const opened = new Promise<void>((resolve) => (release = resolve));
    gates.push(() => release());
    showCustomer('/customer?live=true', [
      http.get('/api/model-info', async () => {
        await opened;
        return HttpResponse.json(modelInfo());
      }),
    ]);
    await loaded();
    const status = await screen.findByText('New customer: 80.3%, Critical band, flagged at 0.36.');
    await screen.findByRole('list', { name: 'Contributions' });
    const said: string[] = [];
    const observer = new MutationObserver(() => said.push(status.textContent));
    observer.observe(status, { childList: true, characterData: true, subtree: true });
    release();
    await pause(700);
    observer.disconnect();
    expect(said).toEqual([]);
    expect(prediction_()).toHaveAttribute('aria-busy', 'false');
    expect(waterfall()).toHaveAttribute('aria-busy', 'false');
  });

  it('refuses an amount outside the API’s bounds without asking, and gives the bounds', async () => {
    const predicted = postsTo('/api/predict');
    await scoreNew();
    await screen.findByText('New customer: 80.3%, Critical band, flagged at 0.36.');
    const tenure = screen.getByRole('textbox', { name: 'Tenure (months)' });
    await userEvent.clear(tenure);
    await userEvent.type(tenure, '500');
    expect(tenure).toHaveAttribute('aria-invalid', 'true');
    expect(tenure).toHaveAccessibleDescription('Tenure is 0 to 120 months.');
    expect(within(prediction_()).getByText('—')).toBeInTheDocument();
    const panel = screen.getByRole('region', { name: 'API response' });
    expect(within(panel).getByText('No response for these inputs.')).toBeInTheDocument();
    await pause(600);
    expect(predicted).toEqual([README_RECORD]);
  });

  it('keeps what was typed when another customer is looked at and the form is opened again', async () => {
    await scoreNew();
    const tenure = screen.getByRole('textbox', { name: 'Tenure (months)' });
    await userEvent.clear(tenure);
    await userEvent.type(tenure, '40');
    await userEvent.click(card(/^DEMO-0005/));
    await userEvent.click(card(/^New customer/));
    expect(screen.getByRole('textbox', { name: 'Tenure (months)' })).toHaveValue('40');
    // Nothing typed is ever put in the address.
    expect(window.location.search).toBe('?live=true');
  });
});

describe('Single customer: when the examples cannot be loaded', () => {
  it('offers a retry, and still opens a linked customer', async () => {
    showCustomer('/customer?id=DEMO-0042', [
      http.get('/api/customers/examples', () => HttpResponse.json(FAILED, { status: 500 })),
    ]);
    await loaded();
    expect(
      await screen.findByText('The example customers could not be loaded.'),
    ).toBeInTheDocument();
    expect(
      await screen.findByText('DEMO-0042: 55.0%, High band, flagged at 0.36.'),
    ).toBeInTheDocument();
    expect(within(prediction_()).getByText('55.0%')).toBeInTheDocument();
    // Without the examples there is no README record to score: its card says so.
    expect(card(/^API example/)).toHaveAccessibleName('API example … … not loaded');
  });

  it('draws nothing for the README example without them, and asks again on request', async () => {
    showCustomer('/customer', [
      http.get('/api/customers/examples', () => HttpResponse.json(FAILED, { status: 500 })),
    ]);
    await loaded();
    expect(
      await screen.findByText('The example customers could not be loaded.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Prediction' })).not.toBeInTheDocument();
    server.use(...customerApi());
    await press(screen.getByRole('button', { name: 'Try again' }));
    expect(document.getElementById('main')).toHaveFocus();
    expect(
      await screen.findByText('API example: 80.3%, Critical band, flagged at 0.36.'),
    ).toBeInTheDocument();
    expect(EXAMPLES).toHaveLength(4);
  });
});
