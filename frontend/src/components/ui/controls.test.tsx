import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { Button, ButtonLink, IconButton } from './Button';
import { Pagination } from './Pagination';
import { nextSort, SortHeader } from './SortHeader';
import { Tabs } from './Tabs';
import { RemovableChip, Segmented, ToggleChip } from './Toggle';

describe('Button', () => {
  it('never submits a form unless asked to', async () => {
    const submit = vi.fn();
    render(
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Button>Export</Button>
      </form>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Export' }));
    expect(submit).not.toHaveBeenCalled();
  });

  it('names an icon-only button for assistive technology', () => {
    render(<IconButton label="Swap rows and columns">↔</IconButton>);
    expect(screen.getByRole('button', { name: 'Swap rows and columns' })).toBeInTheDocument();
  });

  it('changes page without a reload when it is a link inside the app', async () => {
    window.history.replaceState(null, '', '/');
    render(<ButtonLink to="/simulator">Open simulator</ButtonLink>);
    await userEvent.click(screen.getByRole('link', { name: 'Open simulator' }));
    expect(window.location.pathname).toBe('/simulator');
  });
});

describe('ToggleChip and RemovableChip', () => {
  it('reports whether it is on, and asks to flip', async () => {
    const onToggle = vi.fn();
    render(
      <ToggleChip pressed={false} onToggle={onToggle} count="405">
        Critical
      </ToggleChip>,
    );
    const chip = screen.getByRole('button', { name: /Critical/ });
    expect(chip).toHaveAttribute('aria-pressed', 'false');
    expect(chip).toHaveTextContent('405');
    await userEvent.click(chip);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('removes itself under its own accessible name', async () => {
    const onRemove = vi.fn();
    render(
      <RemovableChip label="Clear search" onRemove={onRemove}>
        id ∋ “DEMO”
      </RemovableChip>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Clear search' }));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });
});

describe('Segmented', () => {
  function Measure() {
    const [value, setValue] = useState<'customers' | 'charges'>('customers');
    return (
      <Segmented
        label="Measure"
        value={value}
        onChange={setValue}
        options={[
          { value: 'customers', label: 'Customers' },
          { value: 'charges', label: 'Monthly charges' },
        ]}
      />
    );
  }

  it('marks exactly the chosen option as pressed', async () => {
    render(<Measure />);
    const group = screen.getByRole('group', { name: 'Measure' });
    expect(group).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Monthly charges' }));
    expect(screen.getByRole('button', { name: 'Monthly charges' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Customers' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });
});

describe('Tabs', () => {
  const TABS = [
    { value: 'Contract', label: 'Contract' },
    { value: 'PaymentMethod', label: 'Payment' },
    { value: 'InternetService', label: 'Internet' },
    { value: 'monthly', label: 'Price' },
  ] as const;

  function Segments() {
    const [value, setValue] = useState<(typeof TABS)[number]['value']>('Contract');
    return (
      <Tabs label="Segment" tabs={TABS} value={value} onChange={setValue}>
        <p>Panel for {value}</p>
      </Tabs>
    );
  }

  it('shows the chosen tab’s panel, labelled by its tab', async () => {
    render(<Segments />);
    await userEvent.click(screen.getByRole('tab', { name: 'Price' }));
    expect(screen.getByRole('tab', { name: 'Price' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel', { name: 'Price' })).toHaveTextContent('Panel for monthly');
  });

  it('ties each panel to its tab whatever the tab’s value', async () => {
    function Spaced() {
      const [value, setValue] = useState<'a b' | 'c d'>('a b');
      return (
        <Tabs
          label="Spaced"
          tabs={[
            { value: 'a b', label: 'First' },
            { value: 'c d', label: 'Second' },
          ]}
          value={value}
          onChange={setValue}
        >
          <p>Panel {value}</p>
        </Tabs>
      );
    }
    render(<Spaced />);
    await userEvent.click(screen.getByRole('tab', { name: 'Second' }));
    expect(screen.getByRole('tabpanel', { name: 'Second' })).toHaveTextContent('Panel c d');
  });

  it('keeps only the chosen tab in the tab order', () => {
    render(<Segments />);
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((tab) => tab.getAttribute('tabindex'))).toEqual(['0', '-1', '-1', '-1']);
  });

  it('moves with the arrow keys, wrapping at the ends, and jumps with Home and End', async () => {
    render(<Segments />);
    await userEvent.click(screen.getByRole('tab', { name: 'Contract' }));

    await userEvent.keyboard('{ArrowLeft}');
    expect(screen.getByRole('tab', { name: 'Price' })).toHaveFocus();
    expect(screen.getByRole('tab', { name: 'Price' })).toHaveAttribute('aria-selected', 'true');

    await userEvent.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Contract' })).toHaveAttribute('aria-selected', 'true');

    await userEvent.keyboard('{End}');
    expect(screen.getByRole('tab', { name: 'Price' })).toHaveFocus();
    await userEvent.keyboard('{Home}');
    expect(screen.getByRole('tab', { name: 'Contract' })).toHaveFocus();
  });
});

describe('Pagination', () => {
  it('names the position and cannot go past either end', async () => {
    const onPage = vi.fn();
    const { rerender } = render(
      <Pagination page={1} pages={3} onPage={onPage} note="Showing 1–8 of 20" />,
    );
    expect(screen.getByText('1 / 3')).toBeInTheDocument();
    expect(screen.getByText('Showing 1–8 of 20')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Next page' }));
    expect(onPage).toHaveBeenLastCalledWith(2);

    rerender(<Pagination page={3} pages={3} onPage={onPage} note="" />);
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Previous page' }));
    expect(onPage).toHaveBeenLastCalledWith(2);
  });

  it('steps back from a page past the end to the one before the last', async () => {
    const onPage = vi.fn();
    render(<Pagination page={50} pages={3} onPage={onPage} note="" />);
    expect(screen.getByText('3 / 3')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Previous page' }));
    expect(onPage).toHaveBeenCalledWith(2);
  });

  it('treats an empty result as one page with nowhere to go', () => {
    render(<Pagination page={1} pages={0} onPage={vi.fn()} note="Nothing to show" />);
    expect(screen.getByText('1 / 1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled();
  });

  it('offers page sizes when given them', async () => {
    const onPageSize = vi.fn();
    render(
      <Pagination
        page={1}
        pages={4}
        onPage={vi.fn()}
        note=""
        pageSize={{ value: 15, options: [15, 25, 50], onChange: onPageSize }}
      />,
    );
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Rows per page' }), '50');
    expect(onPageSize).toHaveBeenCalledWith(50);
  });
});

describe('SortHeader', () => {
  it('says how the column sorts, and asks to sort by it', async () => {
    const onSort = vi.fn();
    render(
      <div role="table">
        <div role="row">
          <SortHeader
            column="tenure"
            label="Tenure"
            sort={{ column: 'tenure', dir: 'asc' }}
            onSort={onSort}
          />
          <SortHeader
            column="at_risk"
            label="At risk / mo"
            sort={{ column: 'tenure', dir: 'asc' }}
            onSort={onSort}
          />
        </div>
      </div>,
    );
    const [tenure, atRisk] = screen.getAllByRole('columnheader');
    expect(tenure).toHaveAttribute('aria-sort', 'ascending');
    expect(atRisk).toHaveAttribute('aria-sort', 'none');
    await userEvent.click(screen.getByRole('button', { name: 'At risk / mo' }));
    expect(onSort).toHaveBeenCalledWith('at_risk');
  });

  it('flips the active column and starts a new one at its own default direction', () => {
    const defaults = { tenure: 'asc', at_risk: 'desc' } as const;
    expect(nextSort({ column: 'at_risk', dir: 'desc' }, 'at_risk', defaults)).toEqual({
      column: 'at_risk',
      dir: 'asc',
    });
    expect(nextSort({ column: 'at_risk', dir: 'asc' }, 'at_risk', defaults)).toEqual({
      column: 'at_risk',
      dir: 'desc',
    });
    expect(nextSort({ column: 'at_risk', dir: 'desc' }, 'tenure', defaults)).toEqual({
      column: 'tenure',
      dir: 'asc',
    });
    expect(nextSort({ column: 'tenure', dir: 'asc' }, 'at_risk', defaults)).toEqual({
      column: 'at_risk',
      dir: 'desc',
    });
  });
});
