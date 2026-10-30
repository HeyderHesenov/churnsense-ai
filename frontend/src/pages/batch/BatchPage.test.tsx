import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw/http';
import type { Schemas } from '../../api/client';
import { App } from '../../app/App';
import { downloadText } from '../../lib/download';
import { batchApi, batchRow, batchScore, HELD_OUT, ROWS } from '../../test/batchApi';
import { modelInfo } from '../../test/fixtures';
import { press } from '../../test/keyboard';
import { renderAt } from '../../test/render';
import { answer, refuse, server } from '../../test/server';

vi.mock('../../lib/download', () => ({ downloadText: vi.fn(() => true) }));

const PATH = '/api/batch/score';
const CSV = 'customerID,tenure\nDEMO-00001,3\n';
/** The demo file, as BatchPage imports it. */
const DEMO = '../../../../tests/fixtures/demo_customers.csv?raw';

function csvFile(text = CSV, name = 'crm.csv'): File {
  return new File([text], name, { type: 'text/csv' });
}

/**
 * The page, once the served contract is in (the file box needs it);
 * ``before`` handlers answer ahead of the fixtures.
 */
async function showBatch(before: Parameters<typeof server.use> = [], { contract = true } = {}) {
  server.use(...batchApi());
  if (before.length) server.use(...before);
  const rendered = renderAt(<App />, '/batch');
  await screen.findByRole('heading', { level: 1, name: 'Score a file of customers' });
  if (contract) await screen.findByLabelText('Choose a CSV file to score');
  return rendered;
}

/** Every file sent to be scored, counted as it leaves; ``texts`` reads them, in order. */
function filesSent() {
  const requests: Request[] = [];
  server.events.on('request:start', ({ request }) => {
    if (request.method === 'POST' && new URL(request.url).pathname === PATH) {
      requests.push(request.clone());
    }
  });
  return {
    count: () => requests.length,
    texts: () =>
      Promise.all(
        requests.map(async (request) => {
          // MSW's File is another realm's, so it is read, not checked with instanceof.
          const part = (await request.formData()).get('file');
          return typeof part === 'string' || part === null ? String(part) : part.text();
        }),
      ),
  };
}

/** Released after every test, so a failed one never leaves a request waiting. */
const gates: (() => void)[] = [];
afterEach(() => {
  for (const open of gates.splice(0)) open();
});

/** Requests to score wait until ``release``, then get ``response``. */
function hold(response: Schemas['BatchScoreResponse'] = batchScore()) {
  const requests: Request[] = [];
  let release: () => void = () => undefined;
  const opened = new Promise<void>((resolve) => (release = resolve));
  gates.push(() => release());
  const handler = http.post(PATH, async ({ request }) => {
    requests.push(request);
    await opened;
    return HttpResponse.json(response);
  });
  return { handler, requests, release: () => release() };
}

function refusal(status: number, error: string, problems: string[] = [], headers = {}) {
  const body: Schemas['ErrorResponse'] = { error, detail: 'Refused by a test.', problems };
  return http.post(PATH, () => HttpResponse.json(body, { status, headers }));
}

const input = () => screen.getByLabelText('Choose a CSV file to score');
const validationPanel = () => screen.getByRole('region', { name: 'Validation' });
const status = () => within(validationPanel()).getByRole('status');
const action = (name: string) => within(validationPanel()).getByRole('button', { name });
const table = () => screen.getByRole('table', { name: 'Scored rows' });
const bodyRows = () => within(table()).getAllByRole('row').slice(1);
const results = () => screen.queryByRole('region', { name: 'Results summary' });

async function choose(file = csvFile()) {
  await userEvent.upload(input(), file);
  await waitFor(() => expect(status()).toHaveTextContent(`Ready to score ${file.name}`));
}

async function scoreChosen(file = csvFile()) {
  await choose(file);
  await userEvent.click(action('Score file'));
  await waitFor(() => expect(status()).toHaveTextContent('Accepted: 30 rows, 21 columns'));
}

describe('scoring a file', () => {
  it('sends exactly the chosen file and shows the API’s figures, the riskiest row first', async () => {
    const sent = filesSent();
    await showBatch();
    await scoreChosen();
    expect(await sent.texts()).toEqual([CSV]);
    expect(within(validationPanel()).getByText('200')).toBeInTheDocument();
    const notes = within(validationPanel()).getByRole('list', { name: 'Notes' });
    expect(
      within(notes)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(batchScore().notes);
    expect(screen.queryByText('SYNTHETIC DEMO FILE')).toBeNull();
    const summary = results();
    expect(summary).not.toBeNull();
    expect(within(summary as HTMLElement).getByText('Rows scored').parentElement).toHaveTextContent(
      '30',
    );
    // Row 27 holds the fixture's highest probability, 0.995.
    expect(bodyRows()[0]).toHaveTextContent('DEMO-00027');
    expect(bodyRows()).toHaveLength(12);
  });

  it('scores the synthetic demo file and says it is the demo', async () => {
    const sent = filesSent();
    await showBatch();
    await press(screen.getByRole('button', { name: 'Use the demo file' }));
    await waitFor(() => expect(status()).toHaveTextContent('Ready to score demo_customers.csv'));
    await press(action('Score file'));
    await waitFor(() => expect(status()).toHaveTextContent('Accepted'));
    const [demo] = await sent.texts();
    expect(demo?.startsWith('customerID,gender,SeniorCitizen,')).toBe(true);
    expect(screen.getByText('SYNTHETIC DEMO FILE')).toBeInTheDocument();
  });

  it('sends one request however often the button is pressed while it is pending', async () => {
    const held = hold();
    await showBatch([held.handler]);
    await choose();
    await userEvent.click(action('Score file'));
    await waitFor(() => expect(status()).toHaveTextContent('Scoring crm.csv…'));
    expect(action('Score file')).toHaveAttribute('aria-disabled', 'true');
    await userEvent.dblClick(action('Score file'));
    held.release();
    await waitFor(() => expect(status()).toHaveTextContent('Accepted'));
    expect(held.requests).toHaveLength(1);
  });

  it('says the demo file could not be loaded when its chunk does not arrive', async () => {
    vi.doMock(DEMO, () => {
      throw new Error('offline');
    });
    try {
      await showBatch();
      await userEvent.click(screen.getByRole('button', { name: 'Use the demo file' }));
      await waitFor(() => expect(status()).toHaveTextContent('The demo file could not be loaded'));
      expect(action('Choose another file')).toBeInTheDocument();
    } finally {
      vi.doUnmock(DEMO);
    }
  });

  it('sends one request for two presses that land before the page draws again', async () => {
    const held = hold();
    await showBatch([held.handler]);
    await choose();
    const button = action('Score file');
    act(() => {
      button.click();
      button.click();
    });
    held.release();
    await waitFor(() => expect(status()).toHaveTextContent('Accepted'));
    expect(held.requests).toHaveLength(1);
  });

  it('never shows the answer for a file chosen before the current one', async () => {
    const held = hold();
    await showBatch([held.handler]);
    await choose();
    await userEvent.click(action('Score file'));
    await waitFor(() => expect(held.requests).toHaveLength(1));
    await choose(csvFile('customerID\nDEMO-00002\n', 'next.csv'));
    expect(held.requests[0]?.signal.aborted).toBe(true);
    expect(status()).toHaveTextContent('Ready to score next.csv');
    expect(results()).toBeNull();
  });

  it('stops the request when the file is removed while it is scored', async () => {
    const held = hold();
    await showBatch([held.handler]);
    await choose();
    await userEvent.click(action('Score file'));
    await waitFor(() => expect(held.requests).toHaveLength(1));
    await press(action('Remove file'));
    expect(held.requests[0]?.signal.aborted).toBe(true);
    expect(status()).toHaveTextContent('Waiting for a file');
    expect(input()).toHaveFocus();
  });

  it('stops the request when the page is left', async () => {
    const held = hold();
    const { unmount } = await showBatch([held.handler]);
    await choose();
    await userEvent.click(action('Score file'));
    await waitFor(() => expect(held.requests).toHaveLength(1));
    unmount();
    expect(held.requests[0]?.signal.aborted).toBe(true);
  });

  it('clears the last results as soon as another file is chosen', async () => {
    await showBatch();
    await scoreChosen();
    await userEvent.upload(input(), csvFile('customerID\n', 'other.csv'));
    expect(results()).toBeNull();
  });
});

describe('a file the API does not score', () => {
  it('lists every problem it gives, and how many more there were', async () => {
    const problems = Array.from({ length: 53 }, (_, i) => `'tenure' problem ${String(i)}`);
    await showBatch([refusal(422, 'invalid_input', problems)]);
    await choose();
    await userEvent.click(action('Score file'));
    await waitFor(() => expect(status()).toHaveTextContent('Refused: nothing was scored'));
    const listed = within(validationPanel()).getAllByRole('listitem');
    expect(listed.map((item) => item.textContent)).toEqual([
      ...problems.slice(0, 50),
      'and 3 more',
    ]);
    expect(action('Choose another file')).toBeInTheDocument();
    expect(results()).toBeNull();
  });

  it('says no more problems than it was given', async () => {
    await showBatch([refusal(422, 'invalid_input', ['first', 'second'])]);
    await choose();
    await userEvent.click(action('Score file'));
    const problems = await within(validationPanel()).findByRole('list', { name: 'Problems' });
    expect(
      within(problems)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(['first', 'second']);
  });

  it('says a file the API refused as too large is too large', async () => {
    await showBatch([refusal(413, 'payload_too_large')]);
    await choose();
    await userEvent.click(action('Score file'));
    await waitFor(() => expect(status()).toHaveTextContent('Too large'));
    expect(within(validationPanel()).getByText('413')).toBeInTheDocument();
    expect(validationPanel()).toHaveTextContent('The API refused it');
    expect(action('Choose another file')).toBeInTheDocument();
  });

  it('offers no retry for a request the API refuses outright', async () => {
    await showBatch([refuse('post', PATH, 403, 'forbidden')]);
    await choose();
    await press(action('Score file'));
    await waitFor(() => expect(status()).toHaveTextContent('The API refused the request'));
    expect(within(validationPanel()).getByText('403')).toBeInTheDocument();
    // Any file would be refused alike: the one thing offered is taking the file
    // away, on the button that was pressed, so the focus stays.
    expect(
      within(validationPanel())
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['Remove file']);
    expect(action('Remove file')).toHaveFocus();
    await press(action('Remove file'));
    expect(status()).toHaveTextContent('Waiting for a file');
  });

  it.each([
    ['Choose another file', [refusal(422, 'invalid_input', ['a problem'])]],
    ['Score another file', batchApi()],
  ])('opens the file picker from “%s”', async (name, handlers) => {
    await showBatch(handlers);
    await choose();
    await userEvent.click(action('Score file'));
    const opened = vi.spyOn(input(), 'click').mockImplementation(() => undefined);
    await press(await within(validationPanel()).findByRole('button', { name }));
    expect(opened).toHaveBeenCalledTimes(1);
  });

  it('never sends a file over the served limit, and says it was not sent', async () => {
    const sent = filesSent();
    const limits = { max_batch_rows: 10_000, max_upload_bytes: 10, max_upload_columns: 1000 };
    await showBatch([answer('get', '/api/model-info', modelInfo({ limits }))]);
    await userEvent.upload(input(), csvFile());
    await waitFor(() => expect(status()).toHaveTextContent(`Too large: ${String(CSV.length)} B`));
    expect(within(validationPanel()).getByText('Not sent')).toBeInTheDocument();
    expect(sent.count()).toBe(0);
  });

  it('stages a file exactly at the served limit, as the API takes it', async () => {
    const limits = {
      max_batch_rows: 10_000,
      max_upload_bytes: CSV.length,
      max_upload_columns: 1000,
    };
    await showBatch([answer('get', '/api/model-info', modelInfo({ limits }))]);
    await choose();
  });

  it('sends nothing when the chosen file cannot be read', async () => {
    const sent = filesSent();
    await showBatch();
    const file = csvFile();
    vi.spyOn(file, 'arrayBuffer').mockRejectedValue(new Error('gone'));
    await userEvent.upload(input(), file);
    await waitFor(() => expect(status()).toHaveTextContent('The file could not be read'));
    expect(action('Choose another file')).toBeInTheDocument();
    expect(sent.count()).toBe(0);
  });

  it('refuses several files dropped at once, and sends none', async () => {
    const sent = filesSent();
    await showBatch();
    fireEvent.drop(input(), {
      dataTransfer: { files: [csvFile(), csvFile(CSV, 'b.csv')] },
    });
    expect(status()).toHaveTextContent('2 files were dropped');
    expect(sent.count()).toBe(0);
  });

  it('scores one dropped file like a chosen one', async () => {
    await showBatch();
    fireEvent.drop(input(), { dataTransfer: { files: [csvFile()] } });
    await waitFor(() => expect(status()).toHaveTextContent('Ready to score crm.csv'));
  });

  it('waits out a rate limit, then says the same file can be sent again', async () => {
    const sent = filesSent();
    await showBatch([refusal(429, 'rate_limited', [], { 'Retry-After': '60' })]);
    await choose();
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'], shouldAdvanceTime: true });
    try {
      await userEvent.click(action('Score file'));
      await waitFor(() => expect(status()).toHaveTextContent('Too many files in a short time'));
      // Marked, not ready: a press sends nothing.
      await press(action('Try again'));
      // 10 s short of the wait: more than the test's own timeout, so real time cannot close it.
      act(() => {
        vi.advanceTimersByTime(50_000);
      });
      expect(action('Try again')).toHaveAttribute('aria-disabled', 'true');
      server.use(...batchApi());
      act(() => {
        vi.advanceTimersByTime(10_000);
      });
      expect(status()).toHaveTextContent('Ready to send crm.csv again');
      expect(action('Try again')).not.toHaveAttribute('aria-disabled', 'true');
      await press(action('Try again'));
      await waitFor(() => expect(status()).toHaveTextContent('Accepted'));
    } finally {
      vi.useRealTimers();
    }
    expect(await sent.texts()).toEqual([CSV, CSV]);
  });

  it('sends the same file again after the API failed, and keeps the reader’s place', async () => {
    await showBatch([refuse('post', PATH, 503, 'model_unavailable')]);
    await choose();
    await userEvent.click(action('Score file'));
    await waitFor(() => expect(status()).toHaveTextContent('No model is being served'));
    server.use(...batchApi());
    await press(action('Try again'));
    await waitFor(() => expect(status()).toHaveTextContent('Accepted'));
    expect(action('Score another file')).toHaveFocus();
  });

  it.each([
    [refuse('post', PATH, 500, 'internal_error'), '500'],
    [http.post(PATH, () => HttpResponse.error()), 'No response'],
  ])('names what went wrong when the API fails (%#)', async (handler, code) => {
    await showBatch([handler]);
    await choose();
    await userEvent.click(action('Score file'));
    await waitFor(() => expect(status()).toHaveTextContent('The file could not be scored'));
    expect(within(validationPanel()).getByText(code)).toBeInTheDocument();
    expect(action('Try again')).toBeInTheDocument();
  });

  it('puts the reader back at the file box once the file is removed', async () => {
    await showBatch();
    await choose();
    await userEvent.click(action('Remove file'));
    expect(status()).toHaveTextContent('Waiting for a file');
    expect(input()).toHaveFocus();
  });
});

describe('the scored rows', () => {
  const note = () => screen.getByText(/^Showing \d|^No rows/);
  const next = () => screen.getByRole('button', { name: 'Next page' });
  const perPage = (size: string) =>
    userEvent.selectOptions(screen.getByRole('combobox', { name: 'Rows per page' }), size);

  it('pages through the rows to the last, partial page', async () => {
    await showBatch();
    await scoreChosen();
    expect(note()).toHaveTextContent('Showing 1–12 of 30');
    await userEvent.click(next());
    expect(note()).toHaveTextContent('Showing 13–24 of 30');
    await userEvent.click(next());
    expect(note()).toHaveTextContent('Showing 25–30 of 30');
    expect(next()).toHaveAttribute('aria-disabled', 'true');
  });

  it('keeps the first row shown in view when the page size changes', async () => {
    await showBatch();
    await scoreChosen();
    await perPage('25');
    await userEvent.click(next());
    expect(note()).toHaveTextContent('Showing 26–30 of 30');
    await perPage('12');
    expect(note()).toHaveTextContent('Showing 25–30 of 30');
  });

  it('goes back to the first page when the rows are sorted or filtered', async () => {
    await showBatch();
    await scoreChosen();
    await userEvent.click(next());
    await press(within(table()).getByRole('button', { name: 'Row' }));
    expect(note()).toHaveTextContent('Showing 1–12 of 30');
    await userEvent.click(next());
    await press(screen.getByRole('button', { name: 'Flagged' }));
    const flagged = ROWS.filter((r) => r.flagged).length;
    expect(note()).toHaveTextContent(`Showing 1–12 of ${String(flagged)} flagged`);
  });

  it('sorts by the file’s order when asked', async () => {
    await showBatch();
    await scoreChosen();
    await press(within(table()).getByRole('button', { name: 'Row' }));
    expect(bodyRows()[0]).toHaveTextContent('DEMO-00000');
    expect(within(table()).getAllByRole('columnheader')[0]).toHaveAttribute(
      'aria-sort',
      'ascending',
    );
  });

  it('shows the file’s labels, a held-out test customer’s withheld', async () => {
    await showBatch();
    await scoreChosen();
    await userEvent.click(within(table()).getByRole('button', { name: 'Row' }));
    const cells = (row: number) => within(bodyRows()[row] as HTMLElement).getAllByRole('cell');
    expect(cells(HELD_OUT).at(-1)).toHaveTextContent('withheld');
    expect(cells(0).at(-1)).toHaveTextContent('Yes');
    expect(cells(1).at(-1)).toHaveTextContent('No');
  });

  it('has no label column when labels were not compared, and says why', async () => {
    const reason = 'The dataset is not available, so labels are not compared or shown.';
    const response = batchScore(
      ROWS.map((r) => ({ ...r, label: null })),
      {},
    );
    response.summary = { ...response.summary, labels: null, labels_reason: reason };
    await showBatch([http.post(PATH, () => HttpResponse.json(response))]);
    await scoreChosen();
    expect(within(table()).queryByRole('columnheader', { name: 'Label' })).toBeNull();
    expect(screen.getByText(reason)).toBeInTheDocument();
  });

  it('offers every row back when none is flagged', async () => {
    const calm = batchScore(ROWS.map((r) => batchRow(r.row, { flagged: false })));
    await showBatch([http.post(PATH, () => HttpResponse.json(calm))]);
    await scoreChosen();
    await userEvent.click(screen.getByRole('button', { name: 'Flagged' }));
    expect(screen.getByText('No row is flagged')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Show all rows' }));
    expect(bodyRows()).toHaveLength(12);
    expect(document.getElementById('scored-rows')).toHaveFocus();
  });

  it('gives the API’s reason when the file has too few labelled rows for a rate', async () => {
    await showBatch([http.post(PATH, () => HttpResponse.json(batchScore(ROWS.slice(0, 10))))]);
    await choose();
    await userEvent.click(action('Score file'));
    await waitFor(() => expect(status()).toHaveTextContent('Accepted: 10 rows'));
    expect(results()).toHaveTextContent('A rate needs at least 20 labelled rows.');
  });
});

describe('files to download', () => {
  beforeEach(() => vi.mocked(downloadText).mockClear());

  it('saves the scored CSV exactly as the API built it', async () => {
    await showBatch();
    await scoreChosen();
    await userEvent.click(screen.getByRole('button', { name: 'Scored CSV' }));
    expect(downloadText).toHaveBeenCalledWith('churnsense_batch_predictions.csv', batchScore().csv);
  });

  it('says so when the browser does not save it', async () => {
    vi.mocked(downloadText).mockReturnValueOnce(false);
    await showBatch();
    await scoreChosen();
    await userEvent.click(screen.getByRole('button', { name: 'Scored CSV' }));
    expect(await screen.findByText('The browser did not save the file.')).toBeInTheDocument();
    await choose(csvFile(CSV, 'next.csv'));
    expect(screen.queryByText('The browser did not save the file.')).toBeNull();
  });

  it('hands out no template before the served contract is in', async () => {
    let release: () => void = () => undefined;
    const opened = new Promise<void>((resolve) => (release = resolve));
    gates.push(() => release());
    server.use(
      http.get('/api/model-info', async () => {
        await opened;
        return HttpResponse.json(modelInfo());
      }),
    );
    renderAt(<App />, '/batch');
    const template = await screen.findByRole('button', { name: 'Download template' });
    expect(template).toHaveAttribute('aria-disabled', 'true');
    await userEvent.click(template);
    expect(downloadText).not.toHaveBeenCalled();
    release();
    await screen.findByLabelText('Choose a CSV file to score');
    expect(template).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('hands out a template of the served columns, with no customer in it', async () => {
    await showBatch();
    await userEvent.click(screen.getByRole('button', { name: 'Download template' }));
    const columns = modelInfo().feature_columns.join(',');
    expect(downloadText).toHaveBeenCalledWith('churnsense_template.csv', `customerID,${columns}\n`);
  });
});

describe('the served contract', () => {
  it('lists every column the model scores, with what it may hold', async () => {
    await showBatch();
    await press(screen.getByRole('button', { name: /Required columns \(18\)/ }));
    const listed = screen.getByRole('list', { name: 'Required columns' });
    expect(within(listed).getAllByRole('listitem')).toHaveLength(18);
    expect(within(listed).getByText('0–120, whole')).toBeInTheDocument();
  });

  it('takes no file while the contract cannot be loaded', async () => {
    await showBatch([refuse('get', '/api/model-info', 500, 'internal_error')], { contract: false });
    const upload = screen.getByRole('region', { name: 'Upload' });
    expect(
      await within(upload).findByText(/The served model’s contract could not be loaded/),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('Choose a CSV file to score')).toBeNull();
  });
});
