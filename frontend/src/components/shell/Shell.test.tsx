import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { navigate } from '../../app/router';
import { ROUTES } from '../../app/routes';
import { modelInfo } from '../../test/fixtures';
import { measured, performance } from '../../test/performanceApi';
import { renderAt } from '../../test/render';
import { answer, recordRequests, refuse, server } from '../../test/server';
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
    expect(within(nav).getByRole('link', { name: /^Customer explorer:/ })).toBeInTheDocument();
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
    expect(within(nav).getByRole('link', { name: /^Executive overview:/ })).toHaveAttribute(
      'aria-current',
      'page',
    );

    await userEvent.click(within(nav).getByRole('link', { name: /^Batch scoring:/ }));

    expect(within(nav).getByRole('link', { name: /^Batch scoring:/ })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(within(nav).getByRole('link', { name: /^Executive overview:/ })).not.toHaveAttribute(
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
    expect(within(drawer).getByRole('link', { name: /^Plan a campaign/ })).toHaveFocus();
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
    await userEvent.click(screen.getByRole('link', { name: /^Executive overview:/ }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('closes when a page is chosen', async () => {
    renderShell();
    await userEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }));
    await userEvent.click(screen.getByRole('link', { name: /^Retention simulator:/ }));
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
    expect(within(nav).getByRole('link', { name: /^Customer explorer:/ })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });
});

describe('the sidebar groups', () => {
  const group = (name: string) => screen.getByRole('group', { name });
  const names = (name: string) =>
    within(group(name))
      .getAllByRole('link')
      .map((link) => link.getAttribute('aria-label')?.split(':')[0]);

  it('lists the pages by what a reader goes there to do', () => {
    renderShell();
    expect(names('Understand')).toEqual([
      'Executive overview',
      'Customer explorer',
      'Churn patterns',
    ]);
    expect(names('Trust the model')).toEqual(['Model performance', 'Explainability']);
    expect(names('Take action')).toEqual([
      'Single customer',
      'Retention simulator',
      'Batch scoring',
    ]);
  });

  it('keeps the groups, named, when the sidebar is collapsed', async () => {
    renderShell();
    await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }));
    expect(within(group('Take action')).getByRole('link', { name: /^Single customer:/ })).toBe(
      screen.getByRole('link', { name: /^Single customer:/ }),
    );
    expect(screen.queryByText('Take action')).not.toBeInTheDocument();
  });

  it('lists the single customer under Take action, while its breadcrumb stays under Model', () => {
    // Two places on purpose (routes.ts): the breadcrumb says what a page is about,
    // the sidebar what a reader does there.
    const customer = ROUTES.find((route) => route.path === '/customer');
    expect(customer).toMatchObject({ group: 'Model', navGroup: 'Take action' });
    renderAt(
      <Shell>
        <Header section="Model" page="Single customer" />
      </Shell>,
      '/customer',
    );
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' })).toHaveTextContent(
      /^Model\/Single customer$/,
    );
    expect(
      within(group('Take action')).getByRole('link', { name: /^Single customer:/ }),
    ).toHaveAttribute('aria-current', 'page');
  });

  it('shows each page’s purpose on a second line, and names the link by both', () => {
    renderShell();
    const link = screen.getByRole('link', { name: 'Model performance: How good, where blind' });
    expect(within(link).getByText('Model performance')).toBeInTheDocument();
    expect(within(link).getByText('How good, where blind')).toBeInTheDocument();
  });

  it('keeps the same names on a collapsed rail, where only the icons show', async () => {
    renderShell();
    await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }));
    const link = screen.getByRole('link', { name: 'Model performance: How good, where blind' });
    expect(link).toHaveTextContent(/^$/);
  });
});

describe("the explorer's badge", () => {
  const explorer = () => screen.getByRole('link', { name: /^Customer explorer:/ });

  it('shows the size of the book the service states, and asks for nothing more', async () => {
    const seen = recordRequests();
    server.use(
      answer(
        'get',
        '/api/model-info',
        modelInfo({
          data_source: { customer_records: 'dataset', customers: 7043, title: 'IBM Telco sample' },
        }),
      ),
    );
    renderShell();
    expect(await within(explorer()).findByText('7,043')).toBeInTheDocument();
    expect(explorer()).toHaveAccessibleName(
      'Customer explorer: Every customer, filterable, 7,043 customers',
    );
    expect(new Set(seen.map((url) => url.pathname))).toEqual(
      new Set(['/api/health', '/api/model-info', '/api/performance']),
    );
  });

  it('shows a synthetic book as the service counts it', async () => {
    server.use(
      answer(
        'get',
        '/api/model-info',
        modelInfo({
          data_source: { customer_records: 'synthetic', customers: 2000, title: 'Synthetic demo' },
        }),
      ),
    );
    renderShell();
    expect(await within(explorer()).findByText('2,000')).toBeInTheDocument();
  });

  it('is left out when the service states no data source', async () => {
    renderShell();
    expect(await screen.findByText('isotonic-calibrated, clamped')).toBeInTheDocument();
    expect(explorer()).toHaveAccessibleName('Customer explorer: Every customer, filterable');
    expect(within(explorer()).queryByText(/^\d[\d,]*$/)).not.toBeInTheDocument();
  });

  it('is left out while the model description is refused', async () => {
    server.use(refuse('get', '/api/model-info', 503, 'model_unavailable'));
    renderShell();
    await screen.findByText('Loaded');
    expect(explorer()).toHaveAccessibleName('Customer explorer: Every customer, filterable');
  });

  it('is not shown on a collapsed rail', async () => {
    server.use(
      answer(
        'get',
        '/api/model-info',
        modelInfo({
          data_source: { customer_records: 'dataset', customers: 7043, title: 'IBM Telco sample' },
        }),
      ),
    );
    window.localStorage.setItem('churnsense:sidebar', 'collapsed');
    const { client } = renderShell();
    // The book's size has arrived and been rendered; the rail still shows no badge.
    await waitFor(() => expect(client.getQueryData(['model-info'])).toBeDefined());
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByText('7,043')).not.toBeInTheDocument();
    expect(explorer()).toHaveAccessibleName('Customer explorer: Every customer, filterable');
  });
});

describe('the getting-started guide', () => {
  const guide = () => screen.getByRole('region', { name: 'Getting started' });
  const hide = () => screen.getByRole('button', { name: 'Hide the getting started guide' });

  it('lists three steps and marks the one for this page', () => {
    renderShell('/performance');
    const steps = within(guide()).getAllByRole('link');
    expect(steps.map((step) => step.getAttribute('href'))).toEqual([
      '/',
      '/performance',
      '/simulator',
    ]);
    expect(steps[1]).toHaveAttribute('aria-current', 'step');
    expect(steps[1]).toHaveTextContent('you are here');
    expect(steps[0]).not.toHaveAttribute('aria-current');
    expect(within(guide()).getAllByText('you are here')).toHaveLength(1);
  });

  it('marks no step on a page outside the three', () => {
    renderShell('/batch');
    expect(within(guide()).queryByText('you are here')).not.toBeInTheDocument();
  });

  it('is hidden and remembered through the preference, and nothing else', async () => {
    renderShell();
    await userEvent.click(hide());
    expect(screen.queryByRole('region', { name: 'Getting started' })).not.toBeInTheDocument();
    expect(window.localStorage.getItem('churnsense:guide')).toBe('hidden');
    expect(window.localStorage.getItem('cs_guide_hidden')).toBeNull();
    expect(Object.keys(window.localStorage)).toEqual(['churnsense:guide']);
  });

  it('gives focus to the current page once hidden, not to the document', async () => {
    renderShell('/explorer');
    hide().focus();
    await userEvent.keyboard('{Enter}');
    expect(screen.getByRole('link', { name: /^Customer explorer:/ })).toHaveFocus();
  });

  it('gives focus to the first page on an address that is no page', async () => {
    renderShell('/nowhere');
    hide().focus();
    await userEvent.keyboard('{Enter}');
    expect(screen.getByRole('link', { name: /^Executive overview:/ })).toHaveFocus();
  });

  it('stays hidden on the next visit', () => {
    window.localStorage.setItem('churnsense:guide', 'hidden');
    renderShell();
    expect(screen.queryByRole('region', { name: 'Getting started' })).not.toBeInTheDocument();
    expect(screen.getByText('Serving')).toBeInTheDocument();
  });

  it('still hides when the browser keeps no preference', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    renderShell();
    await userEvent.click(hide());
    expect(screen.queryByRole('region', { name: 'Getting started' })).not.toBeInTheDocument();
  });

  it('is not shown on a collapsed rail', async () => {
    renderShell();
    await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }));
    expect(screen.queryByRole('region', { name: 'Getting started' })).not.toBeInTheDocument();
    // Collapsing is not dismissing.
    expect(window.localStorage.getItem('churnsense:guide')).toBeNull();
  });

  it('closes the drawer when a step is followed, even to the page already shown', async () => {
    setViewport(800);
    renderShell('/');
    await userEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }));
    // The address does not change, so only the step's own handler closes the drawer.
    await userEvent.click(within(guide()).getByRole('link', { name: /^See where risk sits/ }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('the serving card', () => {
  const value = (name: string) => screen.getByText(name).nextElementSibling;

  it('shows the validation ECE, labelled as validation', async () => {
    server.use(
      answer(
        'get',
        '/api/performance',
        performance({ validation_evaluation: measured({ ece: 0.0412 }) }),
      ),
    );
    renderShell();
    await waitFor(() => expect(value('Validation ECE')).toHaveTextContent(/^0\.041$/));
    expect(screen.queryByText('Test ECE')).not.toBeInTheDocument();
  });

  it('shows a dash for an ECE never measured', async () => {
    server.use(answer('get', '/api/performance', performance({ validation_evaluation: null })));
    renderShell();
    await screen.findByText('Loaded');
    await waitFor(() => expect(value('Validation ECE')).toHaveTextContent(/^—$/));
  });

  it('shows a dash when the performance figures are refused', async () => {
    server.use(refuse('get', '/api/performance', 503, 'model_unavailable'));
    const seen = recordRequests();
    renderShell();
    await waitFor(() =>
      expect(seen.filter((url) => url.pathname === '/api/performance')).toHaveLength(1),
    );
    expect(value('Validation ECE')).toHaveTextContent(/^—$/);
    // A failure is not asked again on a change of page: the card stays mounted.
    // (A page that reads the figures itself, or a card mounted afresh after a
    // collapse, asks once more: React Query retries a failed query on mount.)
    for (const path of ['/explorer', '/customer', '/batch', '/']) {
      act(() => navigate(path));
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(seen.filter((url) => url.pathname === '/api/performance')).toHaveLength(1);
    expect(value('Validation ECE')).toHaveTextContent(/^—$/);
  });

  it('asks for the performance figures once, however many pages are visited', async () => {
    const seen = recordRequests();
    renderShell();
    await waitFor(() => expect(value('Validation ECE')).toHaveTextContent(/^0\.031$/));
    for (const path of ['/explorer', '/performance', '/customer', '/', '/batch']) {
      act(() => navigate(path));
    }
    // A collapse unmounts the card and an expand mounts it again: still no new request.
    await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }));
    await userEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    act(() => {
      window.history.back();
    });
    await waitFor(() => expect(value('Validation ECE')).toHaveTextContent(/^0\.031$/));
    expect(seen.filter((url) => url.pathname === '/api/performance')).toHaveLength(1);
  });
});
