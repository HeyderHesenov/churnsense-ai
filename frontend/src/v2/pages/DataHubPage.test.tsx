import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { recordRequests, server } from '../../test/server';
import { pairsJson } from '../api';
import { CSV, mappingRequired, partlyValid, PREVIEW, v2Refuse } from '../test/v2Api';
import { renderV2 } from '../test/render';
import { MAX_STATUS_ROWS } from './dataHub/Mapping';

const file = () => new File([CSV], 'accounts.csv', { type: 'text/csv' });

/** Each form the page posts, in order. */
function recordForms(): FormData[] {
  const forms: FormData[] = [];
  server.events.on('request:start', ({ request }) => {
    if (new URL(request.url).pathname === PREVIEW) {
      void request
        .clone()
        .formData()
        .then((form) => forms.push(form));
    }
  });
  return forms;
}

async function upload() {
  const input = await screen.findByLabelText('Choose a CSV file to check');
  await userEvent.upload(input, file());
}

describe('the mapping pairs', () => {
  it('are written in order, a repeated name kept for the server to refuse', () => {
    expect(
      pairsJson([
        ['a', 'mrr'],
        ['a', 'currency'],
      ]),
    ).toBe('{"a":"mrr","a":"currency"}');
    expect(pairsJson([])).toBe('{}');
    expect(pairsJson([['say "hi"', 'status']])).toBe('{"say \\"hi\\"":"status"}');
  });
});

describe('the Data Hub', () => {
  it('says the file is not saved and lists the contract before a file is chosen', async () => {
    const seen = recordRequests();
    renderV2('/v2/data-hub');
    expect(await screen.findByText('Checked, not saved.')).toBeInTheDocument();
    expect(screen.getByText(/does not feed the portfolio demo/)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/The account contract \(20 fields\)/));
    const fields = screen.getByRole('list', { name: 'Contract fields' });
    expect(within(fields).getAllByRole('listitem')).toHaveLength(20);
    expect(within(fields).getByText('account_external_id').parentElement).toHaveTextContent(
      'required',
    );
    expect(seen).toEqual([]);
  });

  it('sends the file alone first, and shows what the server asks for', async () => {
    const forms = recordForms();
    server.use(http.post(PREVIEW, () => HttpResponse.json(mappingRequired())));
    renderV2('/v2/data-hub');
    await upload();

    expect(await screen.findByText('Map the required fields')).toBeInTheDocument();
    await waitFor(() => expect(forms).toHaveLength(1));
    const [first] = forms;
    expect(await (first?.get('file') as File).text()).toBe(CSV);
    expect(first?.has('mapping')).toBe(false);
    expect(first?.has('status_mapping')).toBe(false);

    expect(screen.getByText(/Required fields not mapped:/).parentElement).toHaveTextContent(
      'Account ID (account_external_id) · required, Snapshot date (as_of_date) · required',
    );
    expect(screen.getByText(/Never read:/).parentElement).toHaveTextContent('email (#6)');
    expect(screen.getByRole('combobox', { name: 'currency (#5)' })).toHaveValue('currency');
    expect(screen.getByRole('combobox', { name: 'account_id (#1)' })).toHaveValue('');
    expect(screen.queryByRole('combobox', { name: /email/ })).toBeNull();
    expect(screen.getByText(/Nothing was imported or stored/)).toBeInTheDocument();
  });

  it('checks the same file again with the reader’s mapping and status translations', async () => {
    const forms = recordForms();
    let calls = 0;
    server.use(
      http.post(PREVIEW, () => {
        calls += 1;
        return HttpResponse.json(calls === 1 ? mappingRequired() : partlyValid());
      }),
    );
    renderV2('/v2/data-hub');
    await upload();
    await screen.findByText('Map the required fields');

    const choose = (column: string, field: string) =>
      userEvent.selectOptions(screen.getByRole('combobox', { name: column }), field);
    await choose('account_id (#1)', 'account_external_id');
    await choose('snapshot_date (#2)', 'as_of_date');
    await choose('plan_state (#3)', 'status');
    await choose('monthly_value (#4)', 'mrr');
    await userEvent.click(screen.getByRole('button', { name: 'Add a status translation' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Value in your file 1' }), 'live');
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Read as' }), 'active');
    await userEvent.click(screen.getByRole('button', { name: 'Check the file with this mapping' }));

    expect(await screen.findByText('Some rows were rejected')).toBeInTheDocument();
    await waitFor(() => expect(forms).toHaveLength(2));
    const second = forms[1];
    expect(await (second?.get('file') as File).text()).toBe(CSV);
    expect(second?.get('mapping')).toBe(
      '{"account_id":"account_external_id","snapshot_date":"as_of_date","plan_state":"status","monthly_value":"mrr","currency":"currency"}',
    );
    expect(second?.get('status_mapping')).toBe('{"live":"active"}');
  });

  it('shows the server’s counts, totals, problems, warnings and rows as given', async () => {
    server.use(http.post(PREVIEW, () => HttpResponse.json(partlyValid())));
    renderV2('/v2/data-hub');
    await upload();
    await screen.findByText('Some rows were rejected');

    const counts = screen.getByRole('group', { name: 'Row counts' });
    expect(within(counts).getByText('Accepted').parentElement).toHaveTextContent('1');
    expect(within(counts).getByText('Rejected').parentElement).toHaveTextContent('1');

    // Once in the totals, once in the accepted row.
    expect(screen.getAllByText('€200.00')).toHaveLength(2);
    expect(screen.getByText('3 more totals not listed.')).toBeInTheDocument();

    expect(
      screen.getByText("status 'live' is not one of the contract statuses."),
    ).toBeInTheDocument();
    expect(
      screen.getByText('12 more not listed: the server keeps the list short.'),
    ).toBeInTheDocument();
    expect(screen.getByText('No renewal date.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'First accepted rows' })).toBeInTheDocument();
    expect(screen.getByText('A-2')).toBeInTheDocument();
    expect(screen.getByText(/Preview only: nothing was stored/)).toBeInTheDocument();
  });

  it('shows the reasons a refused mapping lists, and keeps the last result', async () => {
    let calls = 0;
    server.use(
      http.post(PREVIEW, () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json(mappingRequired())
          : HttpResponse.json(
              {
                error: 'invalid_input',
                detail: 'the column mapping is invalid',
                problems: ["'notes' is mapped to an unknown field"],
              },
              { status: 422 },
            );
      }),
    );
    renderV2('/v2/data-hub');
    await upload();
    await screen.findByText('Map the required fields');
    await userEvent.click(screen.getByRole('button', { name: 'Check the file with this mapping' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The server refused this mapping.');
    expect(within(alert).getByRole('list', { name: 'Reasons' })).toHaveTextContent(
      "'notes' is mapped to an unknown field",
    );
    expect(screen.getByText('Map the required fields')).toBeInTheDocument();
  });

  it('adds and removes status translations, up to the server’s limit', async () => {
    server.use(http.post(PREVIEW, () => HttpResponse.json(mappingRequired())));
    renderV2('/v2/data-hub');
    await upload();
    await screen.findByText('Map the required fields');
    const add = screen.getByRole('button', { name: 'Add a status translation' });
    await userEvent.click(add);
    await userEvent.click(screen.getByRole('button', { name: 'Remove status translation 1' }));
    expect(screen.queryByRole('textbox', { name: /Value in your file/ })).toBeNull();
    for (let i = 0; i < MAX_STATUS_ROWS; i += 1) fireEvent.click(add);
    expect(screen.getAllByRole('textbox', { name: /Value in your file/ })).toHaveLength(100);
    expect(add).toBeDisabled();
  });

  it.each([
    [422, 'The server could not read this file.', ['the file has no header row']],
    [413, 'The file is larger than the local server accepts.', []],
    [404, 'The local server does not have the Data Hub preview on.', []],
  ])('a first check refused with %i says why', async (status, words, problems) => {
    server.use(v2Refuse('post', PREVIEW, status, 'refused', problems));
    renderV2('/v2/data-hub');
    await upload();
    expect(await screen.findByRole('alert')).toHaveTextContent(words);
    for (const problem of problems) expect(screen.getByText(problem)).toBeInTheDocument();
  });

  it('a busy or unreachable server can be retried with the same file', async () => {
    let calls = 0;
    server.use(
      http.post(PREVIEW, () => {
        calls += 1;
        return calls === 1 ? HttpResponse.error() : HttpResponse.json(mappingRequired());
      }),
    );
    renderV2('/v2/data-hub');
    await upload();
    expect(await screen.findByText(/may be unreachable/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Try again/ }));
    expect(await screen.findByText('Map the required fields')).toBeInTheDocument();
  });

  it('a file chosen again while one is checked: only the latest answer is shown', async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let calls = 0;
    const aborted: boolean[] = [];
    server.use(
      http.post(PREVIEW, async ({ request }) => {
        calls += 1;
        if (calls === 1) {
          // The first check is still running when the second file is chosen.
          await held;
          aborted.push(request.signal.aborted);
          return HttpResponse.json(partlyValid());
        }
        return HttpResponse.json(mappingRequired());
      }),
    );
    renderV2('/v2/data-hub');
    await upload();
    await screen.findByText('Checking the file…');
    await userEvent.click(screen.getByRole('button', { name: 'Choose another file' }));
    await upload();
    expect(await screen.findByText('Map the required fields')).toBeInTheDocument();
    release();
    await waitFor(() => expect(aborted).toEqual([true]));
    expect(screen.queryByText('Some rows were rejected')).toBeNull();
    expect(screen.getByText('Map the required fields')).toBeInTheDocument();
  });

  it('leaving the page abandons a check still running', async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const aborted: boolean[] = [];
    server.use(
      http.post(PREVIEW, async ({ request }) => {
        await held;
        aborted.push(request.signal.aborted);
        return HttpResponse.json(mappingRequired());
      }),
    );
    const { unmount } = renderV2('/v2/data-hub');
    await upload();
    await screen.findByText('Checking the file…');
    unmount();
    release();
    await waitFor(() => expect(aborted).toEqual([true]));
  });

  it('another file starts over', async () => {
    server.use(http.post(PREVIEW, () => HttpResponse.json(mappingRequired())));
    renderV2('/v2/data-hub');
    await upload();
    await screen.findByText('Map the required fields');
    await userEvent.click(screen.getByRole('button', { name: 'Choose another file' }));
    expect(screen.getByLabelText('Choose a CSV file to check')).toBeInTheDocument();
    expect(screen.queryByText('Map the required fields')).toBeNull();
  });
});
