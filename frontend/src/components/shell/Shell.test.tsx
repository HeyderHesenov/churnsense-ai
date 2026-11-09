import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { navigate } from '../../app/router';
import { modelInfo } from '../../test/fixtures';
import { renderAt } from '../../test/render';
import { answer, refuse, server } from '../../test/server';
import { setViewport } from '../../test/viewport';
import { Header } from './Header';
import { Shell } from './Shell';

function renderShell(path = '/') {
  return renderAt(
    <Shell>
      <Header section="Book" page="Executive overview" />
    </Shell>,
    path,
  );
}

describe('Shell on a wide screen', () => {
  it('collapses the sidebar to icons and remembers it', async () => {
    renderShell();
    const nav = screen.getByRole('navigation', { name: 'Sections' });
    expect(within(nav).getByText('Customer explorer')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }));

    expect(within(nav).queryByText('Customer explorer')).not.toBeInTheDocument();
    // The link keeps its name for assistive technology once the label is gone.
    expect(within(nav).getByRole('link', { name: 'Customer explorer' })).toBeInTheDocument();
    expect(window.localStorage.getItem('churnsense:sidebar')).toBe('collapsed');
  });

  it('starts collapsed when that was the last choice', () => {
    window.localStorage.setItem('churnsense:sidebar', 'collapsed');
    renderShell();
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('leaves focus alone on the first load', () => {
    renderShell();
    expect(screen.getByRole('main')).not.toHaveFocus();
  });

  it('marks the current page and moves focus to the page after navigating', async () => {
    renderShell();
    const nav = screen.getByRole('navigation', { name: 'Sections' });
    expect(within(nav).getByRole('link', { name: 'Executive overview' })).toHaveAttribute(
      'aria-current',
      'page',
    );

    await userEvent.click(within(nav).getByRole('link', { name: 'Batch scoring' }));

    expect(within(nav).getByRole('link', { name: 'Batch scoring' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(within(nav).getByRole('link', { name: 'Executive overview' })).not.toHaveAttribute(
      'aria-current',
    );
    expect(screen.getByRole('main')).toHaveFocus();
  });

  it('shows what is served, and a dash for a test figure never recorded', async () => {
    renderShell();
    expect(await screen.findByText('Loaded')).toBeInTheDocument();
    expect(screen.getByText('0.36')).toBeInTheDocument();
    expect(screen.getByText('isotonic-calibrated, clamped')).toBeInTheDocument();
    const precision = screen.getByText('Test precision').nextElementSibling;
    expect(precision).toHaveTextContent(/^—$/);
  });

  it('shows the recorded test figures once they exist', async () => {
    server.use(
      answer(
        'get',
        '/api/model-info',
        modelInfo({
          test_evaluation: {
            evaluated_at: '2026-10-24',
            precision: 0.8412,
            recall: 0.7,
          },
        }),
      ),
    );
    renderShell();
    expect(await screen.findByText('0.841')).toBeInTheDocument();
    expect(screen.getByText('0.700')).toBeInTheDocument();
    expect(screen.queryByText('Test ECE')).not.toBeInTheDocument();
    expect(screen.queryByText('Test ROC-AUC')).not.toBeInTheDocument();
  });

  it('says the service cannot be reached when the health check fails', async () => {
    server.use(refuse('get', '/api/health', 500, 'internal_error'));
    renderShell();
    expect(await screen.findByText('Unreachable')).toBeInTheDocument();
  });

  it('claims nothing about the model until its description arrives', async () => {
    server.use(refuse('get', '/api/model-info', 503, 'model_unavailable'));
    renderShell();
    await screen.findByText('Loaded');
    expect(screen.queryByText(/calibrated/)).not.toBeInTheDocument();
  });

  it('offers a skip link to the page as the first stop', async () => {
    renderShell();
    await userEvent.tab();
    const skip = screen.getByRole('link', { name: 'Skip to content' });
    expect(skip).toHaveFocus();
    await userEvent.keyboard('{Enter}');
    expect(screen.getByRole('main')).toHaveFocus();
  });

  it('says so when no model is loaded', async () => {
    server.use(
      answer('get', '/api/health', { status: 'degraded', model_loaded: false, detail: 'x' }),
    );
    renderShell();
    expect(await screen.findByText('No model')).toBeInTheDocument();
  });
});

describe('Shell on a small screen', () => {
  beforeEach(() => setViewport(800));

  it('keeps the closed drawer out of reach', () => {
    renderShell();
    expect(screen.getByRole('complementary', { hidden: true })).toHaveAttribute('inert');
  });

  it('opens the drawer as a dialog and traps focus inside it', async () => {
    renderShell();
    await userEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }));

    const drawer = screen.getByRole('dialog', { name: 'Navigation' });
    const close = within(drawer).getByRole('button', { name: 'Close navigation menu' });
    expect(close).toHaveFocus();

    // Shift+Tab from the first item wraps to the last, Tab from the last back to the first.
    await userEvent.tab({ shift: true });
    expect(within(drawer).getByRole('link', { name: 'Batch scoring' })).toHaveFocus();
    await userEvent.tab();
    expect(close).toHaveFocus();
  });

  it('puts the page behind an open drawer out of reach, and back once it closes', async () => {
    renderShell();
    await userEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }));
    expect(screen.getByRole('main', { hidden: true })).toHaveAttribute('inert');
    await userEvent.keyboard('{Escape}');
    expect(screen.getByRole('main')).not.toHaveAttribute('inert');
  });

  it('closes on Escape and returns focus to the menu button', async () => {
    renderShell();
    const menu = screen.getByRole('button', { name: 'Open navigation menu' });
    await userEvent.click(menu);
    await userEvent.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(menu).toHaveFocus();
    expect(menu).toHaveAttribute('aria-expanded', 'false');
  });

  it('closes when the page already shown is chosen', async () => {
    renderShell('/');
    await userEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }));
    await userEvent.click(screen.getByRole('link', { name: 'Executive overview' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('closes when a page is chosen', async () => {
    renderShell();
    await userEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }));
    await userEvent.click(screen.getByRole('link', { name: 'Retention simulator' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('stops being a drawer when the window widens', async () => {
    renderShell();
    await userEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }));
    act(() => setViewport(1200));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('complementary', { name: 'Navigation' })).not.toHaveAttribute('inert');
    // Narrowing again does not bring back a drawer nobody opened.
    act(() => setViewport(800));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('Shell and history', () => {
  it('follows the back button to the previous page', () => {
    renderShell('/explorer');
    act(() => navigate('/patterns'));
    act(() => {
      window.history.replaceState(null, '', '/explorer');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    const nav = screen.getByRole('navigation', { name: 'Sections' });
    expect(within(nav).getByRole('link', { name: 'Customer explorer' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });
});
