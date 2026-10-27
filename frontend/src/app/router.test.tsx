import { act, render, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  holdDraft,
  Link,
  navigate,
  onNavigate,
  useLocation,
  useSearchParams,
  withParams,
} from './router';

describe('router', () => {
  beforeEach(() => window.history.replaceState(null, '', '/'));

  it('follows navigation and a popstate from the back button', () => {
    const { result } = renderHook(() => useLocation());
    act(() => navigate('/explorer?bands=High'));
    expect(result.current.pathname).toBe('/explorer');
    expect(result.current.search.getAll('bands')).toEqual(['High']);
    act(() => {
      window.history.replaceState(null, '', '/patterns');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(result.current.pathname).toBe('/patterns');
  });

  it('writes a held draft first, once, and not after it is let go', () => {
    const seen: string[] = [];
    holdDraft(() => {
      seen.push(window.location.search);
      navigate('/?q=typed', { replace: true });
    }, 10_000);
    const release = holdDraft(() => seen.push('released'), 10_000);
    release();
    navigate('/?q=typed&view=saved');
    expect(seen).toEqual(['']);
    expect(window.location.search).toBe('?q=typed&view=saved');
    navigate('/');
    expect(seen).toEqual(['']);
  });

  it('writes a draft once it is still, alone, and on its own entry', () => {
    vi.useFakeTimers();
    try {
      const seen: string[] = [];
      holdDraft(() => {
        seen.push('first');
        navigate('/?a=1', { replace: true });
      }, 100);
      holdDraft(() => seen.push('second'), 10_000);
      vi.advanceTimersByTime(100);
      expect(seen).toEqual(['first']);
      expect(window.location.search).toBe('?a=1');
      navigate('/?b=2');
      expect(seen).toEqual(['first', 'second']);
    } finally {
      vi.useRealTimers();
    }
  });

  it('tells a listener about every change of address until it stops listening', () => {
    const heard = vi.fn();
    const stop = onNavigate(heard);
    navigate('/explorer');
    window.dispatchEvent(new PopStateEvent('popstate'));
    stop();
    navigate('/batch');
    expect(heard).toHaveBeenCalledTimes(2);
  });

  it('applies query changes, dropping empty values and repeating lists', () => {
    const location = { pathname: '/explorer', search: new URLSearchParams('q=x&page=3') };
    expect(withParams(location, { q: null, bands: ['High', 'Critical'], page: 1 })).toBe(
      '/explorer?bands=High&bands=Critical&page=1',
    );
    expect(withParams(location, { q: undefined, page: undefined })).toBe('/explorer');
  });

  it('updates the query in place when asked to', () => {
    const { result } = renderHook(() => useSearchParams());
    const before = window.history.length;
    act(() => result.current[1]({ q: 'DEMO' }, { replace: true }));
    expect(window.location.search).toBe('?q=DEMO');
    expect(window.history.length).toBe(before);
  });

  it('intercepts a plain click', async () => {
    render(<Link to="/batch">Batch</Link>);
    const link = screen.getByRole('link', { name: 'Batch' });
    expect(link).toHaveAttribute('href', '/batch');
    await userEvent.click(link);
    expect(window.location.pathname).toBe('/batch');
  });

  it('leaves a modified click to the browser', async () => {
    render(<Link to="/batch">Batch</Link>);
    const user = userEvent.setup();
    await user.keyboard('{Control>}');
    await user.click(screen.getByRole('link', { name: 'Batch' }));
    await user.keyboard('{/Control}');
    expect(window.location.pathname).toBe('/');
  });

  it('applies every change made in one handler, each to the address as it now is', () => {
    const { result } = renderHook(() => useSearchParams());
    act(() => {
      result.current[1]({ sort: 'tenure' });
      result.current[1]({ page: 3 });
    });
    expect(new URLSearchParams(window.location.search).get('sort')).toBe('tenure');
    expect(new URLSearchParams(window.location.search).get('page')).toBe('3');
  });

  it('adds no history entry for an address that only reorders the same query', () => {
    window.history.replaceState(null, '', '/explorer?sort=b&page=2');
    const before = window.history.length;
    act(() => navigate('/explorer?page=2&sort=b'));
    expect(window.history.length).toBe(before);
  });

  it('treats another fragment of the same page as a new address', () => {
    window.history.replaceState(null, '', '/?bands=High');
    const before = window.history.length;
    act(() => navigate('/?bands=High#queue'));
    expect(window.history.length).toBe(before + 1);
    expect(window.location.hash).toBe('#queue');
  });

  it('adds no history entry for the address already shown, fragment included', () => {
    window.history.replaceState(null, '', '/?bands=High#queue');
    const before = window.history.length;
    act(() => navigate('/?bands=High#queue'));
    expect(window.history.length).toBe(before);
  });

  it.each(['//evil.example/path', '/\\evil.example', 'https://evil.example/'])(
    'refuses to leave the app for %s',
    (to) => {
      expect(() => navigate(to)).toThrow(/not an address in this app/);
      expect(() => render(<Link to={to}>Away</Link>)).toThrow(/not an address in this app/);
    },
  );
});
