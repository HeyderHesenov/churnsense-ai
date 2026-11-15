import { screen, waitFor } from '@testing-library/react';
import { modelInfo } from '../../test/fixtures';
import { renderAt, settleQueries } from '../../test/render';
import { answer, server } from '../../test/server';
import { DATASET, SYNTHETIC, syntheticInfo } from '../../test/syntheticSource';
import { OutcomePill, SYNTHETIC_OUTCOME } from './Badges';
import { DataSourceNote, SyntheticTag, useSynthetic } from './DataSourceNote';
import { render } from '@testing-library/react';

function Probe() {
  return <output>{useSynthetic() ? 'synthetic' : 'not synthetic'}</output>;
}

describe('DataSourceNote', () => {
  it('prints the service’s own sentences for the parts a page asks for, in order', async () => {
    server.use(syntheticInfo());
    renderAt(<DataSourceNote parts={['customers', 'figures', 'evidence']} />);
    const note = await screen.findByRole('note', { name: SYNTHETIC.title });
    expect(note).toHaveAttribute('data-source', 'synthetic');
    expect(note).toHaveTextContent(
      `${SYNTHETIC.title}${SYNTHETIC.notice}${SYNTHETIC.figures_note}${SYNTHETIC.evidence_note}`,
    );
  });

  it('prints only the parts asked for', async () => {
    server.use(syntheticInfo());
    renderAt(<DataSourceNote parts={['evidence']} />);
    const note = await screen.findByRole('note');
    expect(note).toHaveTextContent(SYNTHETIC.evidence_note ?? '');
    expect(note).not.toHaveTextContent(SYNTHETIC.notice ?? '');
    expect(note).not.toHaveTextContent(SYNTHETIC.figures_note ?? '');
  });

  it('words nothing itself: other sentences from the service are the ones shown', async () => {
    const other = { ...SYNTHETIC, title: 'Another title', notice: 'Another sentence entirely.' };
    server.use(syntheticInfo(other));
    renderAt(<DataSourceNote parts={['customers']} />);
    expect(await screen.findByRole('note', { name: 'Another title' })).toHaveTextContent(
      'Another titleAnother sentence entirely.',
    );
  });

  it('says nothing where the service gave no sentence for the part', async () => {
    server.use(syntheticInfo({ ...SYNTHETIC, evidence_note: null }));
    renderAt(
      <>
        <DataSourceNote parts={['evidence']} />
        <Probe />
      </>,
    );
    expect(await screen.findByText('synthetic')).toBeInTheDocument();
    expect(screen.queryByRole('note')).toBeNull();
  });

  it.each([
    ['the dataset’s own customers', modelInfo({ data_source: DATASET })],
    ['no book at all', modelInfo({ data_source: null })],
    ['a service that says nothing of its source', modelInfo()],
  ])('renders nothing for %s', async (_name, info) => {
    server.use(answer('get', '/api/model-info', info));
    renderAt(
      <>
        <DataSourceNote parts={['customers', 'figures', 'evidence']} />
        <SyntheticTag />
        <Probe />
      </>,
    );
    await settleQueries();
    expect(screen.getByText('not synthetic')).toBeInTheDocument();
    expect(screen.queryByRole('note')).toBeNull();
    expect(document.querySelector('[data-source]')).toBeNull();
  });

  it('renders nothing before the service has answered', () => {
    server.use(syntheticInfo());
    renderAt(
      <>
        <DataSourceNote parts={['customers']} />
        <Probe />
      </>,
    );
    // Synchronously after mounting: no answer yet, so nothing is claimed either way.
    expect(screen.getByText('not synthetic')).toBeInTheDocument();
    expect(screen.queryByRole('note')).toBeNull();
    return waitFor(() => expect(screen.getByText('synthetic')).toBeInTheDocument());
  });
});

describe('SyntheticTag', () => {
  it('marks a heading with the service’s name for the customers', async () => {
    server.use(syntheticInfo());
    renderAt(<SyntheticTag />);
    const tag = await screen.findByText(SYNTHETIC.title);
    expect(tag).toHaveAttribute('data-source', 'synthetic');
    expect(tag).toHaveAttribute('title', SYNTHETIC.figures_note);
  });
});

describe('OutcomePill for a generated customer', () => {
  it('says the customer is synthetic and has no outcome, never that one is withheld', () => {
    render(<OutcomePill outcome="withheld" partition="synthetic" />);
    const pill = screen.getByText(SYNTHETIC_OUTCOME);
    expect(pill).toHaveAttribute('data-partition', 'synthetic');
    expect(pill).not.toHaveTextContent(/withheld|churned|stayed/i);
  });

  it('never shows an outcome for a synthetic row, whatever outcome it is handed', () => {
    render(<OutcomePill outcome="churned" partition="synthetic" />);
    expect(screen.getByText(SYNTHETIC_OUTCOME)).toBeInTheDocument();
    expect(screen.queryByText(/Churned/)).toBeNull();
  });

  it('keeps a dataset customer’s pill as it was', () => {
    render(<OutcomePill outcome="withheld" partition="test" />);
    expect(screen.getByText('Withheld · test')).toBeInTheDocument();
  });
});
