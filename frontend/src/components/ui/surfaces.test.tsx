import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { SearchField, Select } from './Field';
import { PageHeader } from './PageHeader';
import { Card, CardHeader, SimulatedBadge, StatTile } from './Surface';

describe('Card', () => {
  it('is a region named by its own heading', () => {
    render(
      <Card titleId="campaign">
        <CardHeader id="campaign" title="Campaign at 0.36" subtitle="Validation sweep" />
      </Card>,
    );
    expect(screen.getByRole('region', { name: 'Campaign at 0.36' })).toHaveTextContent(
      'Validation sweep',
    );
    expect(screen.getByRole('heading', { level: 2, name: 'Campaign at 0.36' })).toBeInTheDocument();
  });

  it('says plainly that money figures are simulated', () => {
    render(<SimulatedBadge />);
    expect(screen.getByText('SIMULATED')).toBeInTheDocument();
  });
});

describe('StatTile', () => {
  it('shows a figure with its label and context', () => {
    render(
      <StatTile label="Customers scored" value="7,043" sub="every row, one prediction path" />,
    );
    expect(screen.getByText('Customers scored')).toBeInTheDocument();
    expect(screen.getByText('7,043')).toBeInTheDocument();
  });

  it('as a link, goes where it says and reports the click', async () => {
    window.history.replaceState(null, '', '/');
    const onFollow = vi.fn();
    render(
      <StatTile
        label="Flagged at 0.36"
        value="2,306"
        link={{
          to: '/?bands=High#queue',
          label: 'Flagged at 0.36: 2,306 customers. Show them in the queue',
          onFollow,
        }}
      />,
    );
    await userEvent.click(screen.getByRole('link', { name: /Show them in the queue/ }));
    expect(onFollow).toHaveBeenCalledTimes(1);
    expect(window.location.search).toBe('?bands=High');
  });
});

describe('PageHeader', () => {
  it('gives the page its one top-level heading', () => {
    render(
      <PageHeader eyebrow="Executive overview" title="Where retention risk sits in the book today">
        7,043 customers, scored.
      </PageHeader>,
    );
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      'Where retention risk sits',
    );
  });
});

describe('Field', () => {
  it('labels a search box even when the label is not shown', async () => {
    function Search() {
      const [value, setValue] = useState('');
      return (
        <SearchField
          label="Find a customer by ID"
          value={value}
          onChange={setValue}
          placeholder="Find…"
        />
      );
    }
    render(<Search />);
    const box = screen.getByRole('searchbox', { name: 'Find a customer by ID' });
    await userEvent.type(box, 'DEMO');
    expect(box).toHaveValue('DEMO');
  });

  it('chooses from a labelled select', async () => {
    const onChange = vi.fn();
    render(
      <Select
        label="Partition"
        value="all"
        onChange={onChange}
        options={[
          { value: 'all', label: 'All' },
          { value: 'test', label: 'Test (outcome withheld)' },
        ]}
      />,
    );
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Partition' }), 'test');
    expect(onChange).toHaveBeenCalledWith('test');
  });
});
