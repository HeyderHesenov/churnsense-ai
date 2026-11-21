import { act, render, renderHook, screen } from '@testing-library/react';
import { int, pct } from '../../lib/format';
import { COUNT_UP_MS, easeOutCubic, useCountUp } from '../../lib/useCountUp';
import { setReducedMotion } from '../../test/viewport';
import { CountUp } from './CountUp';

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'performance'] });
});
afterEach(() => {
  vi.useRealTimers();
});

/** Run the animation frames due in the next ``ms``. */
function frames(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

describe('easeOutCubic', () => {
  it('starts at 0, ends at 1, and is past halfway by halfway', () => {
    expect(easeOutCubic(0)).toBe(0);
    expect(easeOutCubic(1)).toBe(1);
    expect(easeOutCubic(0.5)).toBe(0.875);
  });
});

describe('useCountUp', () => {
  it('counts up from 0 and ends exactly on the value', () => {
    const { result } = renderHook(() => useCountUp(7043));
    expect(result.current).toBe(0);
    frames(COUNT_UP_MS / 2);
    expect(result.current).toBeGreaterThan(0);
    expect(result.current).toBeLessThan(7043);
    frames(COUNT_UP_MS);
    expect(result.current).toBe(7043);
  });

  it('ends exactly on a value no sum of steps would reach', () => {
    const value = 0.1 + 0.2;
    const { result } = renderHook(() => useCountUp(value));
    frames(COUNT_UP_MS * 2);
    expect(result.current).toBe(value);
  });

  it('rises the whole way, never past the value', () => {
    const { result } = renderHook(() => useCountUp(500));
    const seen: number[] = [];
    for (let i = 0; i < 80; i += 1) {
      frames(16);
      seen.push(result.current ?? Number.NaN);
    }
    for (let i = 1; i < seen.length; i += 1) {
      expect(seen[i]).toBeGreaterThanOrEqual(seen[i - 1] ?? 0);
      expect(seen[i]).toBeLessThanOrEqual(500);
    }
    expect(seen.at(-1)).toBe(500);
  });

  it('stops asking for frames once it has settled', () => {
    const request = vi.spyOn(window, 'requestAnimationFrame');
    renderHook(() => useCountUp(100));
    frames(COUNT_UP_MS * 2);
    const settled = request.mock.calls.length;
    frames(COUNT_UP_MS);
    expect(request.mock.calls.length).toBe(settled);
  });

  it('gives the value at once, and asks for no frame, when the reader wants less motion', () => {
    setReducedMotion(true);
    const request = vi.spyOn(window, 'requestAnimationFrame');
    const { result, rerender } = renderHook(({ value }) => useCountUp(value), {
      initialProps: { value: 7043 },
    });
    expect(result.current).toBe(7043);
    rerender({ value: 5174 });
    expect(result.current).toBe(5174);
    expect(request).not.toHaveBeenCalled();
  });

  it('jumps to the value when the reader turns motion down part-way', () => {
    const { result } = renderHook(() => useCountUp(900));
    frames(COUNT_UP_MS / 4);
    expect(result.current).toBeLessThan(900);
    act(() => setReducedMotion(true));
    expect(result.current).toBe(900);
  });

  it('starts a new value from where the figure stands', () => {
    const { result, rerender } = renderHook(({ value }) => useCountUp(value), {
      initialProps: { value: 1000 },
    });
    frames(COUNT_UP_MS / 2);
    const standing = result.current ?? 0;
    rerender({ value: 200 });
    frames(16);
    expect(result.current).toBeLessThanOrEqual(standing);
    expect(result.current).toBeGreaterThan(200);
    frames(COUNT_UP_MS * 2);
    expect(result.current).toBe(200);
  });

  it('starts again from 0 after a spell with no figure, with no frame left over', () => {
    const start: { value: number | null } = { value: 5000 };
    const { result, rerender } = renderHook(({ value }) => useCountUp(value), {
      initialProps: start,
    });
    frames(COUNT_UP_MS * 2);
    expect(result.current).toBe(5000);
    rerender({ value: null });
    expect(result.current).toBeNull();
    rerender({ value: 7000 });
    expect(result.current).toBe(0);
    frames(COUNT_UP_MS * 2);
    expect(result.current).toBe(7000);
  });

  it('shows the value, not 0, when the reader turns motion back on', () => {
    setReducedMotion(true);
    const { result } = renderHook(() => useCountUp(900));
    expect(result.current).toBe(900);
    act(() => setReducedMotion(false));
    expect(result.current).toBe(900);
    frames(COUNT_UP_MS);
    expect(result.current).toBe(900);
  });

  it('passes no figure through as none', () => {
    const { result } = renderHook(() => useCountUp(null));
    expect(result.current).toBeNull();
  });

  it('cancels the waiting frame when it leaves the page', () => {
    const cancel = vi.spyOn(window, 'cancelAnimationFrame');
    const { unmount } = renderHook(() => useCountUp(100));
    frames(100);
    unmount();
    expect(cancel).toHaveBeenCalled();
    // Nothing is left to run: no frame calls back into an unmounted hook.
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('CountUp', () => {
  it('gives a screen reader only the value, from the first frame to the last', () => {
    const { container } = render(<CountUp value={7043} format={int} />);
    const read = () => container.querySelector('.sr')?.textContent;
    expect(read()).toBe('7,043');
    const moving = container.querySelector('[aria-hidden="true"]');
    expect(moving).toHaveTextContent('0');
    frames(COUNT_UP_MS / 3);
    expect(read()).toBe('7,043');
    expect(moving?.textContent).not.toBe('7,043');
    frames(COUNT_UP_MS);
    expect(container).toHaveTextContent(/^7,043$/);
    expect(container.querySelector('[aria-hidden]')).not.toBeInTheDocument();
  });

  it('announces nothing: no live region, no status, no alert', () => {
    const { container } = render(<CountUp value={0.265} format={pct} />);
    frames(COUNT_UP_MS / 2);
    expect(container.querySelector('[aria-live]')).not.toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('is one plain text with the value under reduced motion', () => {
    setReducedMotion(true);
    const { container } = render(<CountUp value={0.265} format={pct} />);
    expect(container).toHaveTextContent(/^26\.5%$/);
    expect(container.querySelector('[aria-hidden]')).not.toBeInTheDocument();
  });

  it('shows a dash for no figure, and does not move', () => {
    const request = vi.spyOn(window, 'requestAnimationFrame');
    const { container } = render(<CountUp value={null} format={int} />);
    expect(container).toHaveTextContent(/^—$/);
    expect(request).not.toHaveBeenCalled();
  });

  it('reads a changed value at once while the figure moves to it', () => {
    const { container, rerender } = render(<CountUp value={100} format={int} />);
    frames(COUNT_UP_MS * 2);
    rerender(<CountUp value={250} format={int} />);
    expect(container.querySelector('.sr')).toHaveTextContent('250');
    frames(COUNT_UP_MS * 2);
    expect(container).toHaveTextContent(/^250$/);
  });
});
