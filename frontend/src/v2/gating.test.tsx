/**
 * The V2 Preview exists only in a build made with VITE_CHURNSENSE_V2_PREVIEW=1.
 * Off (the default, and every deployed build): no V2 link, no V2 page and no
 * V2 request; the dashboard is as it was. On: one link in the dashboard's
 * navigation, and the workspace under /v2.
 */
import { screen, within } from '@testing-library/react';
import { overviewApi } from '../test/overviewApi';
import { renderAt } from '../test/render';
import { recordRequests, server } from '../test/server';
import { summary, v2Answer } from './test/v2Api';

const FLAG = 'VITE_CHURNSENSE_V2_PREVIEW';

/** The app as a build with the switch at ``value`` would load it. */
async function appBuiltWith(value: string | undefined) {
  vi.resetModules();
  if (value === undefined) vi.unstubAllEnvs();
  else vi.stubEnv(FLAG, value);
  return (await import('../app/App')).App;
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe('with the preview switch off (the default)', () => {
  beforeEach(() => server.use(...overviewApi()));

  it('is off in the test environment, as in a default build', () => {
    expect(import.meta.env[FLAG]).toBeUndefined();
  });

  it.each(['/v2', '/v2/data-hub', '/v2/portfolio', '/v2/accounts', '/v2/account?id=X'])(
    '%s is no page, and nothing is asked of /api/v2',
    async (path) => {
      const seen = recordRequests();
      const App = await appBuiltWith(undefined);
      renderAt(<App />, path);
      expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(
        'There is no page here',
      );
      expect(seen.filter((url) => url.pathname.startsWith('/api/v2'))).toEqual([]);
    },
  );

  it('the dashboard’s navigation has no V2 link', async () => {
    const App = await appBuiltWith(undefined);
    renderAt(<App />, '/');
    const nav = await screen.findByRole('navigation', { name: 'Sections' });
    expect(within(nav).queryByRole('link', { name: /V2 Preview/ })).toBeNull();
    expect(within(nav).queryByRole('group', { name: 'Preview' })).toBeNull();
  });

  it('a value other than 1 does not turn it on', async () => {
    const App = await appBuiltWith('true');
    renderAt(<App />, '/v2');
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(
      'There is no page here',
    );
  });
});

describe('with the preview switch on', () => {
  it('the dashboard’s navigation has one link to the preview', async () => {
    server.use(...overviewApi());
    const App = await appBuiltWith('1');
    renderAt(<App />, '/');
    const nav = await screen.findByRole('navigation', { name: 'Sections' });
    const links = within(nav).getAllByRole('link', { name: /V2 Preview/ });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute('href', '/v2');
  });

  it('/v2 is the preview, in its own navigation with the way back', async () => {
    const App = await appBuiltWith('1');
    renderAt(<App />, '/v2');
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Retention workspace preview' }),
    ).toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Sections' });
    for (const name of [/^Preview home/, /^Data Hub/, /^Portfolio/, /^Accounts/]) {
      expect(within(nav).getByRole('link', { name })).toBeInTheDocument();
    }
    expect(screen.getByRole('link', { name: 'Back to the dashboard' })).toHaveAttribute(
      'href',
      '/',
    );
  });

  it('a V1 page is unchanged beside it', async () => {
    server.use(...overviewApi());
    const App = await appBuiltWith('1');
    renderAt(<App />, '/explorer');
    const crumbs = await screen.findByRole('navigation', { name: 'Breadcrumb' });
    expect(within(crumbs).getByText('Customer explorer')).toHaveAttribute('aria-current', 'page');
  });

  it('an unknown /v2 address is the preview’s own Not found', async () => {
    const App = await appBuiltWith('1');
    renderAt(<App />, '/v2/nowhere');
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(
      'There is no preview page here',
    );
  });

  it('/v2/portfolio reads the summary', async () => {
    server.use(v2Answer('get', '/api/v2/portfolio/summary', summary()));
    const App = await appBuiltWith('1');
    renderAt(<App />, '/v2/portfolio');
    expect(await screen.findByText('31 October 2026')).toBeInTheDocument();
  });
});
