/**
 * Every page, served as a public demo serves it: generated customers listed,
 * the dataset shown as aggregates. What each page must do then:
 *
 * - say, in the service's own sentences, that the customers are synthetic and
 *   their figures illustrative, wherever a customer or such a figure appears;
 * - mark every listed customer as synthetic, with no outcome;
 * - never offer a partition of the dataset to filter by, or a link to the
 *   dataset's customers;
 * - say where the observed figures come from, on the pages that show them.
 *
 * With the dataset's own customers listed, none of this appears.
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../app/App';
import { SYNTHETIC_OUTCOME } from '../components/ui/Badges';
import { customerApi } from '../test/customerApi';
import { explainApi, globalExplanation } from '../test/explainApi';
import { BOOK, explorerApi } from '../test/explorerApi';
import { modelInfo } from '../test/fixtures';
import { overviewApi, QUEUE_ROWS } from '../test/overviewApi';
import { customerPage, overview } from '../test/overviewFixtures';
import { patternsApi } from '../test/patternsApi';
import { performanceApi } from '../test/performanceApi';
import { renderAt, settleQueries } from '../test/render';
import { answer, respond, server } from '../test/server';
import { sweepApi } from '../test/simulatorApi';
import { asSynthetic, DATASET, SYNTHETIC, syntheticInfo } from '../test/syntheticSource';

const note = () => screen.findByRole('note', { name: SYNTHETIC.title });
const user = () => userEvent.setup({ delay: null });

/** The sentences a note prints, by which the page asked for. */
async function expectNote({
  customers = false,
  figures = false,
  evidence = false,
}: {
  customers?: boolean;
  figures?: boolean;
  evidence?: boolean;
}) {
  const shown = await note();
  const parts: [boolean, string | null | undefined][] = [
    [customers, SYNTHETIC.notice],
    [figures, SYNTHETIC.figures_note],
    [evidence, SYNTHETIC.evidence_note],
  ];
  for (const [wanted, sentence] of parts) {
    if (wanted) expect(shown).toHaveTextContent(sentence ?? '');
    else expect(shown).not.toHaveTextContent(sentence ?? '');
  }
}

describe('Overview, as a public demo', () => {
  function show() {
    const rows = QUEUE_ROWS.map(asSynthetic);
    server.use(
      syntheticInfo(),
      // The queue lists what the service lists: generated customers.
      respond('get', '/api/customers', () =>
        customerPage(rows, { status_counts: { active: rows.length, churned: 0, unknown: 0 } }),
      ),
      ...overviewApi(overview({ customers: SYNTHETIC.customers })),
    );
    return renderAt(<App />, '/');
  }
  const queue = () => screen.getByRole('region', { name: /^Retention queue/ });

  it('says the customers are synthetic, their figures illustrative, and where rates come from', async () => {
    show();
    await expectNote({ customers: true, figures: true, evidence: true });
  });

  it('names the book in its footer as the service names it, not as the dataset', async () => {
    show();
    await note();
    const footer = await screen.findByText(/attributions are\s+signed SHAP/);
    expect(footer).toHaveTextContent(`${SYNTHETIC.title} · 2,000 generated customers`);
    expect(footer).not.toHaveTextContent('IBM Telco Customer Churn');
  });

  it('labels the retention queue, in the service’s words, as illustrative', async () => {
    show();
    await note();
    const heading = within(queue()).getByRole('heading', { name: /^Retention queue/ });
    expect(heading).toHaveTextContent(SYNTHETIC.title);
    expect(queue()).toHaveTextContent(SYNTHETIC.figures_note ?? '');
    // The dataset's sentence about test customers is not said of generated ones.
    expect(queue()).not.toHaveTextContent('Test customers stay listed');
  });

  it('links to everyone flagged without speaking of churned customers: none is recorded', async () => {
    show();
    await note();
    const link = within(queue()).getByRole('link', { name: /^See all [\d,]+ flagged/ });
    expect(link).toHaveAccessibleName(/^See all 2,300 flagged in the explorer$/);
    expect(link).toHaveAttribute('href', '/explorer?flagged=true');
    expect(queue()).not.toHaveTextContent(/churned included/i);
  });

  it('marks every queued customer as synthetic and offers no partition to filter by', async () => {
    show();
    await note();
    await waitFor(() =>
      expect(within(queue()).getAllByText(SYNTHETIC_OUTCOME)).toHaveLength(QUEUE_ROWS.length),
    );
    expect(within(queue()).queryByRole('combobox', { name: 'Partition' })).toBeNull();
    expect(within(queue()).queryByText(/Churned ·|Stayed ·|Withheld ·/)).toBeNull();
  });
});

describe('Explorer, as a public demo', () => {
  function show(path = '/explorer') {
    server.use(syntheticInfo(), ...explorerApi({ book: BOOK.map(asSynthetic) }));
    return renderAt(<App />, path);
  }

  it('says the customers are synthetic and their figures illustrative', async () => {
    show();
    await expectNote({ customers: true, figures: true });
  });

  it('marks every listed customer as synthetic, with no outcome', async () => {
    show();
    await note();
    const table = await screen.findByRole('table', { name: 'Customers' });
    // One pill per listed customer, whatever the page size.
    await waitFor(() => {
      const listed = within(table).getAllByText(/^DEMO-\d{4}$/);
      expect(listed.length).toBeGreaterThan(1);
      expect(within(table).getAllByText(SYNTHETIC_OUTCOME)).toHaveLength(listed.length);
    });
    expect(within(table).queryByText(/Churned ·|Stayed ·|Withheld ·/)).toBeNull();
  });

  it('offers no partition of the dataset to filter by', async () => {
    show();
    await note();
    expect(screen.queryByRole('combobox', { name: 'Partition' })).toBeNull();
    expect(screen.getByRole('combobox', { name: 'Contract' })).toBeInTheDocument();
  });

  it('shows no observed churn for a selection of generated customers', async () => {
    show();
    await note();
    expect(await screen.findByText('synthetic customers have no outcome')).toBeInTheDocument();
    expect(screen.queryByText(/train \+ val/)).toBeNull();
    expect(
      screen.getByText('All 37 synthetic demo customers with their served probability.'),
    ).toBeInTheDocument();
  });

  it('marks an opened customer as synthetic too', async () => {
    show('/explorer?id=DEMO-0003');
    const panel = await screen.findByRole('complementary', { name: 'Customer detail' });
    await waitFor(() => expect(within(panel).getByText(SYNTHETIC_OUTCOME)).toBeInTheDocument());
    expect(within(panel).queryByText(/Churned ·|Stayed ·|Withheld ·/)).toBeNull();
  });
});

describe('Single customer, as a public demo', () => {
  it('says the customers are synthetic', async () => {
    server.use(syntheticInfo(), ...customerApi());
    renderAt(<App />, '/customer');
    await expectNote({ customers: true });
  });
});

describe('Explainability, as a public demo', () => {
  function show() {
    server.use(
      syntheticInfo(),
      ...explainApi({ global: globalExplanation({ partition: 'synthetic' }) }),
    );
    return renderAt(<App />, '/explainability');
  }

  it('says the customers are synthetic and where the dataset’s figures come from', async () => {
    show();
    await expectNote({ customers: true, evidence: true });
  });

  it('names the explained sample and the background as synthetic, never as training customers', async () => {
    show();
    const method = await screen.findByRole('region', { name: 'Method' });
    expect(method).toHaveTextContent('synthetic demo customers, the same on every page');
    expect(method).toHaveTextContent(/synthetic demo customers, seed/);
    expect(method).not.toHaveTextContent(/training customers|validation partition/);
  });
});

describe('Patterns, as a public demo', () => {
  function show(path = '/patterns') {
    server.use(syntheticInfo(), ...patternsApi());
    return renderAt(<App />, path);
  }

  it('says where the observed figures come from, and nothing of listed customers', async () => {
    show();
    await expectNote({ evidence: true });
  });

  it('links no group to the explorer: the dataset’s customers are not there to open', async () => {
    show();
    const table = await screen.findByRole('table');
    await note();
    const group = within(table).getByRole('button', {
      description: (text) => text.startsWith('Contract One year, tenure 4–72 months'),
    });
    await user().click(group);
    expect(group).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('link', { name: /Open these/ })).toBeNull();
    expect(screen.queryByText(/customer explorer/)).toBeNull();
  });

  it('promises no listing of training customers for a hidden group either', async () => {
    show('/patterns?pr=Month-to-month&pc=0-3');
    await screen.findByRole('table');
    await note();
    expect(screen.queryByText(/still list training customers/)).toBeNull();
  });
});

describe('Performance and the simulator, as a public demo', () => {
  it('performance says where its figures come from', async () => {
    server.use(syntheticInfo(), performanceApi());
    renderAt(<App />, '/performance');
    await screen.findByRole('table', { name: 'Metrics by partition' });
    await expectNote({ evidence: true });
  });

  it('the simulator says where its sweep comes from', async () => {
    server.use(syntheticInfo(), sweepApi());
    renderAt(<App />, '/simulator');
    await screen.findByRole('heading', { level: 1, name: 'Where should the cut-off sit?' });
    await expectNote({ evidence: true });
  });
});

describe('Every page, with the dataset’s own customers listed', () => {
  it.each([
    ['/', () => overviewApi()],
    ['/explorer', () => explorerApi()],
    ['/customer', () => customerApi()],
    ['/explainability', () => explainApi()],
    ['/patterns', () => patternsApi()],
    ['/performance', () => [performanceApi()]],
    ['/simulator', () => [sweepApi()]],
  ])('%s shows no synthetic notice or mark', async (path, api) => {
    server.use(answer('get', '/api/model-info', modelInfo({ data_source: DATASET })), ...api());
    renderAt(<App />, path);
    await screen.findByRole('heading', { level: 1 });
    await settleQueries();
    expect(document.querySelector('[data-source]')).toBeNull();
    expect(screen.queryByText(SYNTHETIC_OUTCOME)).toBeNull();
  });

  it('the explorer still offers the partition filter', async () => {
    server.use(answer('get', '/api/model-info', modelInfo({ data_source: DATASET })));
    server.use(...explorerApi());
    renderAt(<App />, '/explorer');
    expect(await screen.findByRole('combobox', { name: 'Partition' })).toBeInTheDocument();
  });
});
