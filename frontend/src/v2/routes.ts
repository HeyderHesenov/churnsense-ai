/**
 * The V2 Preview's pages. The sidebar groups them by where their data comes
 * from - your own file, or the synthetic demo - so the two are never read as
 * one experience. Only the M1 Data Hub and the M2 Workbench have pages: the
 * Action Center (M3) and Impact & Experiments (M4) are contracts, not services.
 */
import { lazy, type ComponentType, type LazyExoticComponent } from 'react';
import type { icons } from '../components/icons';

export const V2_GROUPS = ['Start', 'Your file', 'Synthetic demo'] as const;

export interface V2Route {
  path: string;
  label: string;
  description: string;
  group: (typeof V2_GROUPS)[number];
  icon: keyof typeof icons;
  page: LazyExoticComponent<ComponentType>;
  /** A page reached from another (an account from the list): the link it lights up. */
  under?: string;
}

export const V2_ROUTES: readonly V2Route[] = [
  {
    path: '/v2',
    label: 'Preview home',
    description: 'What this preview holds',
    group: 'Start',
    icon: 'overview',
    page: lazy(() => import('./pages/HomePage')),
  },
  {
    path: '/v2/data-hub',
    label: 'Data Hub',
    description: 'Check an account file',
    group: 'Your file',
    icon: 'upload',
    page: lazy(() => import('./pages/DataHubPage')),
  },
  {
    path: '/v2/portfolio',
    label: 'Portfolio',
    description: 'Signals and MRR by currency',
    group: 'Synthetic demo',
    icon: 'performance',
    page: lazy(() => import('./pages/PortfolioPage')),
  },
  {
    path: '/v2/accounts',
    label: 'Accounts',
    description: 'Prioritised by the rules',
    group: 'Synthetic demo',
    icon: 'explorer',
    page: lazy(() => import('./pages/AccountsPage')),
  },
  {
    path: '/v2/account',
    label: 'Account',
    description: 'One account’s evidence',
    group: 'Synthetic demo',
    icon: 'customer',
    page: lazy(() => import('./pages/AccountPage')),
    under: '/v2/accounts',
  },
];

export function v2RouteFor(pathname: string): V2Route | undefined {
  return V2_ROUTES.find((route) => route.path === pathname);
}
