import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { entriesAdded } from '../test/history';
import { navigate, useSearchParams } from './router';
import { useSettledDraft } from './useSettledDraft';

/** A box whose text lives in the address under ``name``, as a filter's does. */
function Box({ name }: { name: string }) {
  const [search, update] = useSearchParams();
  const [draft, setDraft] = useSettledDraft(search.get(name) ?? '', (text) =>
    update({ [name]: text || null }, { replace: true }),
  );
  return (
    <input aria-label={name} value={draft} onChange={(event) => setDraft(event.target.value)} />
  );
}

/** A discrete change, writing ``q`` (to its current value or another) unless it is left out. */
function Apply({ q }: { q?: string | null }) {
  const [, update] = useSearchParams();
  return (
    <button
      type="button"
      onClick={() => update(q === undefined ? { view: 'saved' } : { q, view: 'saved' })}
    >
      Apply
    </button>
  );
}

function search() {
  return new URLSearchParams(window.location.search);
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 300));

describe('useSettledDraft', () => {
  beforeEach(() => window.history.replaceState(null, '', '/'));

  it('writes what is typed once it is still, in place', async () => {
    render(<Box name="q" />);
    const added = entriesAdded();
    await userEvent.type(screen.getByRole('textbox', { name: 'q' }), 'abc');
    expect(search().get('q')).toBeNull();
    await waitFor(() => expect(search().get('q')).toBe('abc'));
    expect(added()).toBe(0);
  });

  it('writes nothing when it mounts, so a linked address stays as it is', async () => {
    window.history.replaceState(null, '', '/?q=abc&page=2');
    render(<Box name="q" />);
    expect(screen.getByRole('textbox', { name: 'q' })).toHaveValue('abc');
    await act(settle);
    expect(window.location.search).toBe('?q=abc&page=2');
  });

  it('writes what is typed before any other change, into the entry it was typed in', async () => {
    render(
      <>
        <Box name="q" />
        <Apply q={null} />
      </>,
    );
    const box = screen.getByRole('textbox', { name: 'q' });
    await userEvent.type(box, 'abc');
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(window.location.search).toBe('?view=saved');
    expect(box).toHaveValue('');
    await act(settle);
    expect(window.location.search).toBe('?view=saved');

    act(() => window.history.back());
    await waitFor(() => expect(window.location.search).toBe('?q=abc'));
    await waitFor(() => expect(box).toHaveValue('abc'));
  });

  it('carries what is typed into a change that leaves the box alone', async () => {
    render(
      <>
        <Box name="q" />
        <Apply />
      </>,
    );
    const box = screen.getByRole('textbox', { name: 'q' });
    await userEvent.type(box, 'abc');
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(search().get('q')).toBe('abc');
    expect(search().get('view')).toBe('saved');
    expect(box).toHaveValue('abc');
    act(() => window.history.back());
    await waitFor(() => expect(window.location.search).toBe('?q=abc'));
  });

  it('keeps both of two boxes typed in turn', async () => {
    render(
      <>
        <Box name="min" />
        <Box name="max" />
      </>,
    );
    const added = entriesAdded();
    await userEvent.type(screen.getByRole('textbox', { name: 'min' }), '3');
    await userEvent.type(screen.getByRole('textbox', { name: 'max' }), '9');
    await waitFor(() => expect(search().get('max')).toBe('9'));
    expect(search().get('min')).toBe('3');
    expect(screen.getByRole('textbox', { name: 'min' })).toHaveValue('3');
    expect(added()).toBe(0);
  });

  it('lets one box settle without writing another the reader is still typing in', async () => {
    render(
      <>
        <Box name="min" />
        <Box name="max" />
      </>,
    );
    const pause = () => new Promise((resolve) => setTimeout(resolve, 150));
    await userEvent.type(screen.getByRole('textbox', { name: 'min' }), '3');
    await act(pause);
    await userEvent.type(screen.getByRole('textbox', { name: 'max' }), '1');
    await act(pause);
    expect(search().get('min')).toBe('3');
    expect(search().get('max')).toBeNull();
    await userEvent.type(screen.getByRole('textbox', { name: 'max' }), '2');
    await waitFor(() => expect(search().get('max')).toBe('12'));
  });

  it('drops what is typed when the reader goes back: that entry is no longer shown', async () => {
    render(<Box name="q" />);
    act(() => navigate('/?view=saved'));
    const box = screen.getByRole('textbox', { name: 'q' });
    await userEvent.type(box, 'abc');
    act(() => window.history.back());
    await waitFor(() => expect(window.location.search).toBe(''));
    await waitFor(() => expect(box).toHaveValue(''));
    await act(settle);
    expect(window.location.search).toBe('');
  });

  it('shows a value written elsewhere in place of what was typed', async () => {
    render(
      <>
        <Box name="q" />
        <Apply q="zzz" />
      </>,
    );
    const box = screen.getByRole('textbox', { name: 'q' });
    await userEvent.type(box, 'abc');
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(box).toHaveValue('zzz');
    await act(settle);
    expect(search().get('q')).toBe('zzz');
  });
});
