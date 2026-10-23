import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Shell } from '../components/shell/Shell';
import { renderAt } from '../test/render';
import { App, PageBoundary } from './App';
import { navigate } from './router';

describe('App', () => {
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

describe('PageBoundary', () => {
  function Broken(): never {
    throw new Error('render failed');
  }

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
      '/explorer?bands=High',
    );
    expect(screen.getByRole('navigation', { name: 'Sections' })).toBeInTheDocument();
    quiet.mockRestore();
  });
});
