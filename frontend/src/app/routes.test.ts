import { NAV_GROUPS, ROUTES, routeFor } from './routes';

describe('routes', () => {
  it('gives each page a breadcrumb section and a sidebar group, kept apart on purpose', () => {
    expect(ROUTES.map(({ path, group, navGroup }) => [path, group, navGroup])).toEqual([
      ['/', 'Book', 'Understand'],
      ['/explorer', 'Book', 'Understand'],
      ['/patterns', 'Book', 'Understand'],
      ['/performance', 'Model', 'Trust the model'],
      ['/explainability', 'Model', 'Trust the model'],
      // The one page whose two places differ: about the model, used to act.
      ['/customer', 'Model', 'Take action'],
      ['/simulator', 'Act', 'Take action'],
      ['/batch', 'Act', 'Take action'],
    ]);
  });

  it('lists the sidebar groups in the order the pages come', () => {
    const order = ROUTES.map((route) => NAV_GROUPS.indexOf(route.navGroup));
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(new Set(ROUTES.map((route) => route.navGroup))).toEqual(new Set(NAV_GROUPS));
  });

  it('describes every page in a line of its own', () => {
    for (const route of ROUTES) expect(route.description).toMatch(/^\S.{3,40}\S$/);
    expect(new Set(ROUTES.map((route) => route.description)).size).toBe(ROUTES.length);
  });

  it('finds a page by its exact address only', () => {
    expect(routeFor('/customer')?.label).toBe('Single customer');
    expect(routeFor('/customer/')).toBeUndefined();
    expect(routeFor('/nowhere')).toBeUndefined();
  });
});
