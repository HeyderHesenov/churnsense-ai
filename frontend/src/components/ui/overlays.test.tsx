import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChartFrame } from '../charts/ChartFrame';
import { AttributionChip, BandLabel, OutcomePill, ProbabilityMeter } from './Badges';
import { Popover } from './Popover';
import { press } from '../../test/keyboard';
import { EmptyState, ErrorState, Skeleton } from './States';

describe('Popover', () => {
  function Provenance() {
    return (
      <>
        <Popover
          label="Data provenance"
          trigger={(props) => <button {...props}>Provenance verified</button>}
        >
          <p>Dataset SHA-256</p>
          <a href="/performance">How the split is checked</a>
        </Popover>
        <button type="button">Elsewhere</button>
      </>
    );
  }

  it('opens as a labelled dialog and says it is open', async () => {
    render(<Provenance />);
    const trigger = screen.getByRole('button', { name: 'Provenance verified' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('dialog', { name: 'Data provenance' })).toHaveTextContent(
      'Dataset SHA-256',
    );
    expect(trigger).toHaveAttribute('aria-controls', screen.getByRole('dialog').id);
  });

  it('takes focus when it opens, so its contents are read next', async () => {
    render(<Provenance />);
    await userEvent.click(screen.getByRole('button', { name: 'Provenance verified' }));
    expect(screen.getByRole('dialog', { name: 'Data provenance' })).toHaveFocus();
  });

  it('closes on Escape and gives focus back to its button', async () => {
    render(<Provenance />);
    const trigger = screen.getByRole('button', { name: 'Provenance verified' });
    await userEvent.click(trigger);
    await userEvent.tab();
    expect(screen.getByRole('link', { name: 'How the split is checked' })).toHaveFocus();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('closes on a click outside it, and stays open on a click inside', async () => {
    render(<Provenance />);
    await userEvent.click(screen.getByRole('button', { name: 'Provenance verified' }));
    await userEvent.click(screen.getByText('Dataset SHA-256'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Elsewhere' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('toggles closed from its own button', async () => {
    render(<Provenance />);
    const trigger = screen.getByRole('button', { name: 'Provenance verified' });
    await userEvent.click(trigger);
    await userEvent.click(trigger);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('States', () => {
  it('announces loading in words, not the placeholder shapes', () => {
    render(<Skeleton label="Loading customers" rows={3} />);
    // A live region is announced by its content, not its name.
    expect(screen.getByRole('status')).toHaveTextContent('Loading customers');
  });

  it('explains an empty result and offers the way back', async () => {
    const onReset = vi.fn();
    render(
      <EmptyState title="No band selected" action={{ label: 'Reset filters', onClick: onReset }}>
        Choose at least one band to see flagged customers.
      </EmptyState>,
    );
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent('No band selected');
    expect(status).toHaveTextContent('Choose at least one band');
    await userEvent.click(screen.getByRole('button', { name: 'Reset filters' }));
    expect(onReset).toHaveBeenCalledTimes(1);
  });

  it('reports a failure with a retry, in its own words, and keeps the reader’s place', async () => {
    const onRetry = vi.fn();
    render(
      <>
        <h2 id="customers-title" tabIndex={-1}>
          Customers
        </h2>
        <ErrorState onRetry={onRetry} focusTo="customers-title">
          The customers could not be loaded.
        </ErrorState>
      </>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('The customers could not be loaded.');
    // Asking again takes the button away (the query goes back to loading), so the
    // reader continues where the page says.
    await press(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('heading', { name: 'Customers' })).toHaveFocus();
  });
});

describe('Badges', () => {
  it.each([
    ['churned', 'train', 'Churned · train'],
    ['stayed', 'validation', 'Stayed · val'],
    ['withheld', 'test', 'Withheld · test'],
    ['withheld', 'unknown', 'Withheld · unverified'],
  ] as const)('words outcome %s in partition %s as “%s”', (outcome, partition, text) => {
    render(<OutcomePill outcome={outcome} partition={partition} />);
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it('colours an attribution by the sign it shows, and a zero by neither', () => {
    render(
      <>
        <AttributionChip label="Tenure" value={0.317} />
        <AttributionChip label="Monthly" value={-0.209} />
        <AttributionChip label="Phone" value={-0.0004} />
      </>,
    );
    expect(screen.getByText('Tenure +31.7')).toHaveAttribute('data-direction', 'raises');
    expect(screen.getByText('Monthly −20.9')).toHaveAttribute('data-direction', 'lowers');
    expect(screen.getByText('Phone 0.0')).toHaveAttribute('data-direction', 'none');
  });

  it('labels a band by name, its colour only a decoration', () => {
    render(<BandLabel band="Critical" />);
    expect(screen.getByText('Critical')).toBeInTheDocument();
  });

  it('shows a probability with the clamp-aware precision', () => {
    render(<ProbabilityMeter value={0.99988} band="Critical" />);
    expect(screen.getByText('0.9999')).toBeInTheDocument();
  });
});

describe('ChartFrame', () => {
  it('gives the chart one accessible name and its figures as a table', () => {
    render(
      <ChartFrame
        label="Customers per 0.05 of probability"
        table={{
          caption: 'Customers per bin',
          columns: ['Bin', 'Customers'],
          rows: [['0.00–0.05', '1,021']],
        }}
      >
        <svg viewBox="0 0 10 10">
          <rect width="10" height="10" />
        </svg>
      </ChartFrame>,
    );
    expect(
      screen.getByRole('img', { name: 'Customers per 0.05 of probability' }),
    ).toBeInTheDocument();
    const table = screen.getByRole('table', { name: 'Customers per bin' });
    expect(table).toHaveTextContent('1,021');
  });
});
