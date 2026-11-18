import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Disclosure } from './Disclosure';
import { InfoNote } from './InfoNote';

describe('InfoNote', () => {
  it('is a note: its point in bold, then the detail, read in order', () => {
    render(
      <InfoNote title="A re-used holdout, not an untouched one.">
        The test set was scored when the model was chosen.
      </InfoNote>,
    );
    const note = screen.getByRole('note');
    expect(note).toHaveTextContent(
      'A re-used holdout, not an untouched one. The test set was scored when the model was chosen.',
    );
    expect(screen.getByText('A re-used holdout, not an untouched one.').tagName).toBe('STRONG');
  });

  it('is never announced: no alert, no live region', () => {
    render(<InfoNote title="Simulated figures." />);
    const note = screen.getByRole('note');
    expect(note).not.toHaveAttribute('aria-live');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('hides its icon from screen readers and takes no focus', async () => {
    render(<InfoNote title="Simulated figures.">Nothing here is measured.</InfoNote>);
    expect(screen.getByRole('note').querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    await userEvent.tab();
    expect(document.body).toHaveFocus();
  });

  it('has no trailing space without a detail', () => {
    render(<InfoNote title="Simulated figures." />);
    expect(screen.getByRole('note').textContent).toBe('Simulated figures.');
  });
});

describe('Disclosure', () => {
  function setup(defaultOpen?: boolean) {
    render(
      <Disclosure summary="Required columns (18)" {...(defaultOpen ? { defaultOpen } : {})}>
        <p>customerID, gender, SeniorCitizen</p>
      </Disclosure>,
    );
    const summary = screen.getByText('Required columns (18)');
    const details = summary.closest('details');
    if (!details) throw new Error('no details element');
    return { summary, details };
  }

  it('is the browser’s own details and summary, closed at first', () => {
    const { summary, details } = setup();
    expect(summary.tagName).toBe('SUMMARY');
    expect(details).not.toHaveAttribute('open');
    expect(screen.getByText('customerID, gender, SeniorCitizen')).not.toBeVisible();
  });

  it('opens and closes with a press on its summary', async () => {
    const { summary, details } = setup();
    await userEvent.click(summary);
    expect(details).toHaveAttribute('open');
    expect(screen.getByText('customerID, gender, SeniorCitizen')).toBeVisible();
    await userEvent.click(summary);
    expect(details).not.toHaveAttribute('open');
  });

  it('is reached with Tab, on its summary', async () => {
    const { summary } = setup();
    await userEvent.tab();
    expect(summary).toHaveFocus();
  });

  it('can be open when first shown, and closed by the reader', async () => {
    const { summary, details } = setup(true);
    expect(details).toHaveAttribute('open');
    await userEvent.click(summary);
    expect(details).not.toHaveAttribute('open');
  });

  it('keeps the reader’s choice when its page draws again', async () => {
    const view = (note: string) => (
      <Disclosure summary="Required columns (18)" defaultOpen>
        <p>{note}</p>
      </Disclosure>
    );
    const { rerender } = render(view('first'));
    const details = screen.getByText('Required columns (18)').closest('details');
    await userEvent.click(screen.getByText('Required columns (18)'));
    expect(details).not.toHaveAttribute('open');
    rerender(view('second'));
    expect(details).not.toHaveAttribute('open');
    expect(screen.getByText('second')).toBeInTheDocument();
  });

  it('hides its +/− marker from screen readers', () => {
    const { summary } = setup();
    const marker = summary.querySelector('[aria-hidden="true"]');
    expect(marker).toBeInTheDocument();
    expect(marker).toBeEmptyDOMElement();
    expect(summary).toHaveTextContent(/^Required columns \(18\)$/);
  });
});
