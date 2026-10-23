import { act, render, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, navigate, useLocation, useSearchParams, withParams } from './router';

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
