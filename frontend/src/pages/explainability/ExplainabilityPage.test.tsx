import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw/http';
import type { CustomerDetail } from '../../api/queries';
import { App } from '../../app/App';
import { MAIN_ID } from '../../components/shell/Shell';
import { probabilityPct, shownSign, signedPoints } from '../../lib/format';
import { valueText } from '../../lib/labels';
import { EXAMPLES, LINKED } from '../../test/customerApi';
import {
  explainApi,
  explainResponse,
  explanationOf,
  globalExplanation,
} from '../../test/explainApi';
import { modelInfo } from '../../test/fixtures';
import { entriesAdded } from '../../test/history';
import { renderAt } from '../../test/render';
import { answer, recordRequests, refuse, server } from '../../test/server';
import {
  EXPLAIN_FAILED,
  EXPLAIN_UNAVAILABLE,
  FAILED,
  incomparable,
  MASKING_CAVEAT,
  MISMATCH,
  NOT_CACHED,
  notFound,
  PROVENANCE_UNVERIFIED,
  TITLE,
} from './copy';

const [FIRST, SECOND] = EXAMPLES as [CustomerDetail, CustomerDetail, ...CustomerDetail[]];

/** The page at ``path`` with the fixture API; ``handlers`` go first, so they answer. */
async function showExplain(path = '/explainability', handlers: Parameters<typeof server.use> = []) {
  server.use(...handlers, ...explainApi());
  const rendered = renderAt(<App />, path);
  await screen.findByRole('heading', { level: 1, name: TITLE });
  return rendered;
}

const rows = () => within(screen.getByRole('group', { name: 'Attributes' })).getAllByRole('button');
const row = (label: string) =>
  within(screen.getByRole('group', { name: 'Attributes' })).getByRole('button', {
    name: (name) => name.startsWith(`${label},`),
  });
const card = (name: string | RegExp) => screen.getByRole('region', { name });
const customerCard = () => card('One customer');
const slider = () => screen.getByRole('slider', { name: 'Group' });

/** A hold on ``POST /api/explain`` for ``id``: answered only when released. */
function holdExplain(id: string) {
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => (release = resolve));
  const handler = http.post('/api/explain', async ({ request }) => {
    const body = (await request.clone().json()) as { customer_ids?: string[] };
    if (body.customer_ids?.[0] !== id) return undefined;
    await gate;
    const customer = [...EXAMPLES, LINKED].find((c) => c.id === id) as CustomerDetail;
    return HttpResponse.json(explainResponse([explanationOf(customer)]));
  });
  return { handler, release: () => release() };
}

describe('the cached attributions', () => {
  it('lists every attribute in the served ranking, each with its served figure and rank', async () => {
    await showExplain();
    const names = rows().map((b) => b.getAttribute('aria-label'));
    const served = globalExplanation().importance;
    expect(names).toHaveLength(18);
    expect(names[0]).toBe(
      `Tenure, mean absolute contribution ${((served[0]?.mean_abs_shap ?? 0) * 100).toFixed(1)} percentage points, rank 1`,
    );
    expect(names.map((n) => n?.split(',')[0])).toEqual(served.map((r) => r.label));
  });

  it('states the method in the served figures', async () => {
    await showExplain();
    const method = card('Method');
    for (const text of ['25.8%', 'validation partition, seed 42', '12', '100', '10', '1.9e-15'])
      expect(method).toHaveTextContent(text);
  });

  it('studies the top attribute first, and another one picked without a history entry', async () => {
    await showExplain();
    expect(row('Tenure')).toHaveAttribute('aria-pressed', 'true');
    expect(card('Tenure: value against contribution')).toBeInTheDocument();
    const added = entriesAdded();
    await userEvent.click(row('Contract'));
    expect(row('Contract')).toHaveAttribute('aria-pressed', 'true');
    expect(row('Tenure')).toHaveAttribute('aria-pressed', 'false');
    expect(card('Contract: value against contribution')).toBeInTheDocument();
    expect(window.location.search).toBe('?f=Contract');
    expect(added()).toBe(0);
    // Back to the top of the ranking: the address is clean again.
    await userEvent.click(row('Tenure'));
    expect(window.location.search).toBe('');
  });

  it('falls back to the top attribute for one the cache does not explain', async () => {
    await showExplain('/explainability?f=customerID');
    expect(row('Tenure')).toHaveAttribute('aria-pressed', 'true');
  });

  it('steps through the served groups by keyboard, saying when a group is too small', async () => {
    await showExplain();
    const groups = slider();
    expect(groups).toHaveAttribute(
      'aria-valuetext',
      'Tenure 1 to 6.9 months: 5 customers, mean contribution +13.8 pp',
    );
    fireEvent.change(groups, { target: { value: '1' } });
    expect(slider()).toHaveAttribute(
      'aria-valuetext',
      'Tenure over 6.9, up to 12.8 months: 1 customer, too few for a mean',
    );
    fireEvent.change(slider(), { target: { value: slider().getAttribute('max') } });
    expect(slider().getAttribute('aria-valuetext')).toMatch(
      /^Tenure over 66\.1, up to 72 months: 2 customers/,
    );
    await userEvent.click(row('Contract'));
    expect(slider()).toHaveAttribute(
      'aria-valuetext',
      'Contract Month-to-month: 6 customers, mean contribution +1.3 pp',
    );
    const table = screen.getByRole('table', { name: 'Contract: groups' });
    expect(within(table).getAllByRole('row')).toHaveLength(4);
    expect(table).toHaveTextContent('One year4too few for a mean');
  });

  it('marks a group’s mean while it is stepped to, and no mean the server withheld', async () => {
    await showExplain();
    fireEvent.focus(slider());
    expect(screen.getByTestId('group-mark')).toBeInTheDocument();
    fireEvent.change(slider(), { target: { value: '1' } });
    // "too few for a mean": nothing is drawn where a mean would be.
    expect(screen.queryByTestId('group-mark')).not.toBeInTheDocument();
    fireEvent.blur(slider());
    fireEvent.change(slider(), { target: { value: '0' } });
    expect(screen.queryByTestId('group-mark')).not.toBeInTheDocument();
  });

  it('closes with the masking caveat and the server’s own', async () => {
    await showExplain();
    expect(screen.getByText(MASKING_CAVEAT)).toBeInTheDocument();
    expect(
      screen.getByText('These are model attributions, not causal effects.'),
    ).toBeInTheDocument();
  });

  it('draws a ranking line only for an attribute ranked both ways, and picks by it', async () => {
    await showExplain();
    const rankings = card('Two rankings, both published');
    const chips = within(rankings).getAllByRole('button');
    expect(chips).toHaveLength(7);
    expect(chips[0]).toHaveTextContent('Contract#1 → #2');
    const added = entriesAdded();
    await userEvent.click(within(rankings).getByRole('button', { name: /^Online security/ }));
    expect(row('Online security')).toHaveAttribute('aria-pressed', 'true');
    expect(added()).toBe(0);
  });
});

describe('the states of the page', () => {
  it('asks once while nothing is cached, and again when asked', async () => {
    const seen = recordRequests();
    server.use(refuse('get', '/api/explain/global', 503, 'explanations_not_cached'));
    renderAt(<App />, '/explainability');
    expect(await screen.findByText(NOT_CACHED.title)).toBeInTheDocument();
    const global = () => seen.filter((u) => u.pathname === '/api/explain/global').length;
    expect(global()).toBe(1);
    server.use(...explainApi());
    await userEvent.click(screen.getByRole('button', { name: 'Check again' }));
    expect(await screen.findAllByRole('button', { pressed: true })).not.toHaveLength(0);
    expect(global()).toBe(2);
    // The button went with the empty state: the focus waits on the page, not the body.
    expect(document.activeElement?.id).toBe(MAIN_ID);
  });

  it('says it failed, retries, and keeps the focus on the page', async () => {
    server.use(refuse('get', '/api/explain/global', 500, 'internal_error'));
    renderAt(<App />, '/explainability');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(FAILED);
    server.use(...explainApi());
    await userEvent.click(within(alert).getByRole('button', { name: 'Try again' }));
    expect(document.activeElement?.id).toBe(MAIN_ID);
    expect(await screen.findByRole('group', { name: 'Attributes' })).toBeInTheDocument();
  });

  it('says it failed when the model cannot be described, and asks for no customer', async () => {
    const seen = recordRequests();
    server.use(refuse('get', '/api/model-info', 500, 'internal_error'), ...explainApi());
    renderAt(<App />, `/explainability?id=${FIRST.id}`);
    expect(await screen.findByRole('alert')).toHaveTextContent(FAILED);
    expect(screen.queryByRole('group', { name: 'Attributes' })).not.toBeInTheDocument();
    expect(seen.some((u) => u.pathname === '/api/explain')).toBe(false);
  });

  it('hides every figure when the cache and the model name different artifacts, until reloaded', async () => {
    const seen = recordRequests();
    server.use(
      answer('get', '/api/model-info', modelInfo({ model_sha256: 'b'.repeat(64) })),
      ...explainApi(),
    );
    renderAt(<App />, `/explainability?id=${FIRST.id}`);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(MISMATCH);
    expect(screen.queryByRole('group', { name: 'Attributes' })).not.toBeInTheDocument();
    // Nor is any customer asked for while the two disagree.
    expect(seen.some((u) => u.pathname === '/api/explain')).toBe(false);
    server.use(answer('get', '/api/model-info', modelInfo()));
    await userEvent.click(within(alert).getByRole('button', { name: 'Reload figures' }));
    expect(document.activeElement?.id).toBe(MAIN_ID);
    expect(await screen.findByRole('group', { name: 'Attributes' })).toBeInTheDocument();
  });
});

describe('one customer', () => {
  it('offers the examples as links, with their probability and never their outcome', async () => {
    await showExplain();
    const picks = await within(customerCard()).findByRole('navigation', {
      name: 'Example customers',
    });
    const links = within(picks).getAllByRole('link');
    expect(links.map((l) => l.textContent)).toEqual(
      EXAMPLES.map((c) => `${c.id}${probabilityPct(c.churn_probability)}`),
    );
    expect(links[0]).toHaveAttribute('href', `/explainability?id=${FIRST.id}`);
    expect(picks).not.toHaveTextContent(/stayed|churned|withheld/i);
    expect(links.filter((l) => l.hasAttribute('aria-current'))).toHaveLength(0);
  });

  it('marks the placed example as the current one', async () => {
    await showExplain(`/explainability?id=${SECOND.id}`);
    const picks = await within(customerCard()).findByRole('navigation', {
      name: 'Example customers',
    });
    const current = within(picks)
      .getAllByRole('link')
      .filter((l) => l.getAttribute('aria-current') === 'page');
    expect(current.map((l) => l.textContent)).toEqual([
      `${SECOND.id}${probabilityPct(SECOND.churn_probability)}`,
    ]);
  });

  it('places a linked customer on every chart, from their own explanation', async () => {
    await showExplain(`/explainability?id=${FIRST.id}`);
    const explained = explanationOf(FIRST);
    const summary = await within(customerCard()).findByRole('table', {
      name: `${FIRST.id}: every contribution`,
    });
    expect(within(summary).getAllByRole('row')).toHaveLength(19);
    expect(customerCard()).toHaveTextContent('25.8%');
    expect(customerCard()).toHaveTextContent(probabilityPct(FIRST.churn_probability));
    const tenure = explained.contributions.find((c) => c.feature === 'tenure');
    const swarm = screen.getByRole('table', { name: 'Attributes by mean absolute contribution' });
    expect(swarm).toHaveTextContent(signedPoints(tenure?.shap_value ?? 0));
    expect(screen.getAllByTestId('ring')).toHaveLength(19);
    expect(within(customerCard()).getByRole('link', { name: /full waterfall/ })).toHaveAttribute(
      'href',
      `/customer?id=${FIRST.id}`,
    );
  });

  it('names the three contributions that raise and that lower the estimate most', async () => {
    await showExplain(`/explainability?id=${FIRST.id}`);
    await within(customerCard()).findByRole('table', { name: `${FIRST.id}: every contribution` });
    const { contributions } = explanationOf(FIRST);
    const named = (sign: 1 | -1) =>
      contributions
        .filter((c) => shownSign(c.shap_value) === sign)
        .slice(0, 3)
        .map((c) => `${c.label} ${signedPoints(c.shap_value)}`);
    expect(named(1)).toHaveLength(3);
    expect(named(-1)).toHaveLength(3);
    const figure = (term: string) =>
      within(customerCard()).getByText(term).nextElementSibling?.textContent;
    expect(figure('Raises the estimate most (pp)')).toBe(named(1).join(', '));
    expect(figure('Lowers it most (pp)')).toBe(named(-1).join(', '));
  });

  it('writes each value of the customer the way the page writes values', async () => {
    await showExplain(`/explainability?id=${FIRST.id}`);
    const summary = await within(customerCard()).findByRole('table', {
      name: `${FIRST.id}: every contribution`,
    });
    const cells = within(summary)
      .getAllByRole('row')
      .slice(1)
      .map((r) => within(r).getAllByRole('cell')[0]?.textContent);
    const { contributions } = explanationOf(FIRST);
    expect(cells).toEqual(contributions.map((c) => valueText(c.feature, c.value)));
    expect(cells).not.toEqual(contributions.map((c) => String(c.value)));
  });

  it('never shows one customer’s explanation under another’s', async () => {
    const held = holdExplain(FIRST.id);
    await showExplain(`/explainability?id=${FIRST.id}`, [held.handler]);
    await userEvent.click(
      await within(customerCard()).findByRole('link', {
        name: (name) => name.startsWith(SECOND.id),
      }),
    );
    await within(customerCard()).findByRole('table', { name: `${SECOND.id}: every contribution` });
    held.release();
    await new Promise((r) => setTimeout(r, 30));
    expect(
      within(customerCard()).queryByRole('table', { name: `${FIRST.id}: every contribution` }),
    ).not.toBeInTheDocument();
    expect(customerCard()).toHaveTextContent(probabilityPct(SECOND.churn_probability));
    expect(
      within(customerCard()).getByText('Served probability').nextElementSibling,
    ).toHaveTextContent(probabilityPct(SECOND.churn_probability));
  });

  it('asks for no customer before the cached attributions are in', async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => (release = resolve));
    const seen = recordRequests();
    // One call, the held answer first: of one call's handlers the first answers.
    server.use(
      http.get('/api/explain/global', async () => {
        await gate;
        return HttpResponse.json(globalExplanation());
      }),
      ...explainApi(),
    );
    renderAt(<App />, `/explainability?id=${FIRST.id}`);
    await new Promise((r) => setTimeout(r, 50));
    expect(seen.some((u) => u.pathname === '/api/explain')).toBe(false);
    release();
    await within(await screen.findByRole('region', { name: 'One customer' })).findByRole('table', {
      name: `${FIRST.id}: every contribution`,
    });
  });

  it('asks for no customer while the training data is unverified, and says why', async () => {
    const seen = recordRequests();
    server.use(
      answer(
        'get',
        '/api/model-info',
        modelInfo({
          provenance: {
            verified: false,
            data_sha256: null,
            split_digest: null,
            withheld_reason: 'x',
          },
        }),
      ),
    );
    await showExplain(`/explainability?id=${FIRST.id}`);
    expect(customerCard()).toHaveTextContent(PROVENANCE_UNVERIFIED);
    expect(within(customerCard()).getByRole('textbox', { name: 'Customer ID' })).toBeDisabled();
    expect(seen.some((u) => u.pathname === '/api/explain')).toBe(false);
  });

  it('stops asking once the server says it can explain no one', async () => {
    const seen = recordRequests();
    await showExplain(`/explainability?id=${FIRST.id}`, [
      refuse('post', '/api/explain', 503, 'explanations_unavailable'),
    ]);
    expect(await within(customerCard()).findByText(EXPLAIN_UNAVAILABLE)).toBeInTheDocument();
    const asked = () => seen.filter((u) => u.pathname === '/api/explain').length;
    expect(asked()).toBe(1);
    // No way to ask is left: the examples are no longer links and the ID field is off.
    const picks = within(customerCard()).getByRole('navigation', { name: 'Example customers' });
    expect(within(picks).queryAllByRole('link')).toHaveLength(0);
    expect(within(customerCard()).getByRole('textbox', { name: 'Customer ID' })).toBeDisabled();
    await new Promise((r) => setTimeout(r, 30));
    expect(asked()).toBe(1);
    // Nor for another customer named in the address later.
    act(() => {
      window.history.pushState(null, '', `/explainability?id=${SECOND.id}`);
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(await within(customerCard()).findByText(EXPLAIN_UNAVAILABLE)).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 30));
    expect(asked()).toBe(1);
  });

  it('says when a typed customer is not in the book', async () => {
    await showExplain();
    const added = entriesAdded();
    await userEvent.type(
      within(customerCard()).getByRole('textbox', { name: 'Customer ID' }),
      'DEMO 9999!',
    );
    await userEvent.click(within(customerCard()).getByRole('button', { name: 'Place customer' }));
    expect(window.location.search).toBe('?id=DEMO9999');
    expect(added()).toBe(1);
    expect(await within(customerCard()).findByText(notFound('DEMO9999'))).toBeInTheDocument();
    expect(screen.queryAllByTestId('ring')).toHaveLength(0);
  });

  it('keeps the placed customer when the typed ID holds nothing an ID can', async () => {
    await showExplain(`/explainability?id=${FIRST.id}`);
    await within(customerCard()).findByRole('table', { name: `${FIRST.id}: every contribution` });
    const added = entriesAdded();
    await userEvent.type(
      within(customerCard()).getByRole('textbox', { name: 'Customer ID' }),
      ' !?',
    );
    await userEvent.click(within(customerCard()).getByRole('button', { name: 'Place customer' }));
    expect(window.location.search).toBe(`?id=${FIRST.id}`);
    expect(added()).toBe(0);
    expect(screen.getAllByTestId('ring')).toHaveLength(19);
  });

  it('asks again once after a rate limit, then lets the reader ask', async () => {
    let refused = 0;
    await showExplain(`/explainability?id=${FIRST.id}`, [
      http.post('/api/explain', () => {
        if (refused >= 2) return undefined;
        refused += 1;
        return HttpResponse.json(
          { error: 'rate_limited', detail: 'x', problems: [] },
          { status: 429, headers: { 'Retry-After': '0' } },
        );
      }),
    ]);
    expect(await within(customerCard()).findByText(EXPLAIN_FAILED)).toBeInTheDocument();
    expect(refused).toBe(2);
    await userEvent.click(
      within(customerCard()).getByRole('button', { name: 'Ask for the attributions again' }),
    );
    expect(document.activeElement).toBe(
      within(customerCard()).getByRole('heading', { name: 'One customer' }),
    );
    await within(customerCard()).findByRole('table', { name: `${FIRST.id}: every contribution` });
  });

  it.each([
    ['artifact', { model_sha256: 'b'.repeat(64) }],
    ['background', { background_customers: 99 }],
  ] as const)('rings no one measured another way (%s)', async (reason, overrides) => {
    await showExplain(`/explainability?id=${FIRST.id}`, [
      http.post('/api/explain', () =>
        HttpResponse.json(explainResponse([explanationOf(FIRST)], overrides)),
      ),
    ]);
    expect(await within(customerCard()).findByText(incomparable(reason))).toBeInTheDocument();
    expect(screen.queryAllByTestId('ring')).toHaveLength(0);
  });

  it('offers to reload when a customer is explained by another artifact than the figures', async () => {
    await showExplain(`/explainability?id=${FIRST.id}`, [
      http.post('/api/explain', () =>
        HttpResponse.json(
          explainResponse([explanationOf(FIRST)], { model_sha256: 'b'.repeat(64) }),
        ),
      ),
    ]);
    const alert = await within(customerCard()).findByRole('alert');
    expect(alert).toHaveTextContent(incomparable('artifact'));
    // Its figures belong to another model than the page's: none is shown beside them.
    expect(
      within(customerCard()).queryByRole('table', { name: `${FIRST.id}: every contribution` }),
    ).not.toBeInTheDocument();
    server.use(...explainApi());
    await userEvent.click(within(alert).getByRole('button', { name: 'Reload figures' }));
    expect(document.activeElement?.id).toBe(MAIN_ID);
    await within(customerCard()).findByRole('table', { name: `${FIRST.id}: every contribution` });
  });

  it('drops a shown customer at once when another is picked, before their answer comes', async () => {
    const held = holdExplain(SECOND.id);
    await showExplain(`/explainability?id=${FIRST.id}`, [held.handler]);
    await within(customerCard()).findByRole('table', { name: `${FIRST.id}: every contribution` });
    await userEvent.click(
      await within(customerCard()).findByRole('link', {
        name: (name) => name.startsWith(SECOND.id),
      }),
    );
    expect(
      within(customerCard()).queryByRole('table', { name: `${FIRST.id}: every contribution` }),
    ).not.toBeInTheDocument();
    expect(within(customerCard()).getByRole('status')).toHaveTextContent(
      `Loading the attributions of ${SECOND.id}`,
    );
    expect(screen.queryAllByTestId('ring')).toHaveLength(0);
    held.release();
    await within(customerCard()).findByRole('table', { name: `${SECOND.id}: every contribution` });
  });

  it('shows nothing of an answer that names another customer', async () => {
    await showExplain(`/explainability?id=${SECOND.id}`, [
      http.post('/api/explain', () => HttpResponse.json(explainResponse([explanationOf(FIRST)]))),
    ]);
    expect(await within(customerCard()).findByText(EXPLAIN_FAILED)).toBeInTheDocument();
    expect(within(customerCard()).queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryAllByTestId('ring')).toHaveLength(0);
  });

  it('rings no one measured from another base value', async () => {
    const shifted = { ...explanationOf(FIRST), base_value: 0.2 };
    await showExplain(`/explainability?id=${FIRST.id}`, [
      http.post('/api/explain', () => HttpResponse.json(explainResponse([shifted]))),
    ]);
    expect(await within(customerCard()).findByText(incomparable('baseline'))).toBeInTheDocument();
    expect(screen.queryAllByTestId('ring')).toHaveLength(0);
    // Its own base value is stated, not the cached one.
    expect(within(customerCard()).getByText('Base value').nextElementSibling).toHaveTextContent(
      '20.0%',
    );
  });

  it('clears the customer, and the rings go with them', async () => {
    await showExplain(`/explainability?id=${FIRST.id}`);
    await waitFor(() => expect(screen.getAllByTestId('ring')).toHaveLength(19));
    await userEvent.click(within(customerCard()).getByRole('button', { name: 'Clear' }));
    expect(window.location.search).toBe('');
    expect(screen.queryAllByTestId('ring')).toHaveLength(0);
  });

  it('links the header to the single-customer page', async () => {
    await showExplain();
    expect(screen.getByRole('link', { name: /Explain one customer/ })).toHaveAttribute(
      'href',
      '/customer',
    );
  });
});
