import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Shell } from '../components/shell/Shell';
import { overviewApi } from '../test/overviewApi';
import { renderAt } from '../test/render';
import { server } from '../test/server';
import { App, PageBoundary } from './App';
import { navigate } from './router';

describe('App', () => {
  // Every test may land on the overview, which reads its own API.
  beforeEach(() => server.use(...overviewApi()));

  it('shows the page the address names, inside the shell', async () => {
    renderAt(<App />, '/explorer');
    const crumbs = await screen.findByRole('navigation', { name: 'Breadcrumb' });
    expect(within(crumbs).getByText('Customer explorer')).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('navigation', { name: 'Sections' })).toBeInTheDocument();
  });

  it('changes page when the address does', async () => {
    renderAt(<App />, '/');
    await screen.findByText('Executive overview', { selector: '[aria-current="page"]:not(a)' });
    act(() => navigate('/batch'));
    expect(
      await screen.findByText('Batch scoring', { selector: '[aria-current="page"]:not(a)' }),
    ).toBeInTheDocument();
  });

  it('says so for an address that is no page, with the way back', async () => {
    renderAt(<App />, '/nowhere');
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(
      'There is no page here',
    );
    await userEvent.click(screen.getByRole('link', { name: 'Go to the executive overview' }));
    expect(window.location.pathname).toBe('/');
  });
});

describe('an address that only looks like another site', () => {
  beforeEach(() => server.use(...overviewApi()));

  it('is no page, and the way back stays on this origin', async () => {
    const origin = window.location.origin;
    renderAt(<App />, '/.//evil.example/explorer');
    expect(window.location.pathname).toBe('//evil.example/explorer');
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(
      'There is no page here',
    );
    await userEvent.click(screen.getByRole('link', { name: 'Go to the executive overview' }));
    expect(window.location.href).toBe(`${origin}/`);
  });
});

describe('PageBoundary', () => {
  function Broken(): never {
    throw new Error('render failed');
  }

  it('offers a fresh load of this very address, never another site', () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    renderAt(
      <Shell>
        <PageBoundary section="Book" page="Customer explorer">
          <Broken />
        </PageBoundary>
      </Shell>,
      '/.//evil.example/explorer?a=1',
    );
    const link = screen.getByRole('link', { name: 'Reload the page' });
    const target = new URL(link.getAttribute('href') ?? '', window.location.href);
    expect(target.origin).toBe(window.location.origin);
    expect(target.pathname).toBe('//evil.example/explorer');
    quiet.mockRestore();
  });

  it('reloads an address with a fragment too: following the fragment would only scroll', () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    renderAt(
      <Shell>
        <PageBoundary section="Book" page="Customer explorer">
          <Broken />
        </PageBoundary>
      </Shell>,
      '/explorer?bands=High#customers',
    );
    expect(screen.getByRole('link', { name: 'Reload the page' })).toHaveAttribute(
      'href',
      `${window.location.origin}/explorer?bands=High`,
    );
    quiet.mockRestore();
  });

  it('keeps a failing page from taking the app down, and offers a fresh load', () => {
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    renderAt(
      <Shell>
        <PageBoundary section="Book" page="Customer explorer">
          <Broken />
        </PageBoundary>
      </Shell>,
      '/explorer?bands=High',
    );
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('This page could not be shown.');
    expect(alert).not.toHaveTextContent('render failed');
    // A plain link: a full load fetches the current build, which also
    // recovers a page whose code could not be downloaded.
    expect(within(alert).getByRole('link', { name: 'Reload the page' })).toHaveAttribute(
      'href',
      `${window.location.origin}/explorer?bands=High`,
    );
    expect(screen.getByRole('navigation', { name: 'Sections' })).toBeInTheDocument();
    quiet.mockRestore();
  });
});
