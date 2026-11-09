/** The eight pages, in the sidebar's order and groups. */
import { lazy, type ComponentType, type LazyExoticComponent } from 'react';
import type { icons } from '../components/icons';

export interface Route {
  path: string;
  label: string;
  group: 'Book' | 'Model' | 'Act';
  icon: keyof typeof icons;
  page: LazyExoticComponent<ComponentType>;
}

export const ROUTES: readonly Route[] = [
  {
    path: '/',
    label: 'Executive overview',
    group: 'Book',
    icon: 'overview',
    page: lazy(() => import('../pages/overview/OverviewPage')),
  },
  {
    path: '/explorer',
    label: 'Customer explorer',
    group: 'Book',
    icon: 'explorer',
    page: lazy(() => import('../pages/explorer/ExplorerPage')),
  },
  {
    path: '/patterns',
    label: 'Churn patterns',
    group: 'Book',
    icon: 'patterns',
    page: lazy(() => import('../pages/patterns/PatternsPage')),
  },
  {
    path: '/performance',
    label: 'Model performance',
    group: 'Model',
    icon: 'performance',
    page: lazy(() => import('../pages/performance/PerformancePage')),
  },
  {
    path: '/explainability',
    label: 'Explainability',
    group: 'Model',
    icon: 'explainability',
    page: lazy(() => import('../pages/explainability/ExplainabilityPage')),
  },
  {
    path: '/customer',
    label: 'Single customer',
    group: 'Model',
    icon: 'customer',
    page: lazy(() => import('../pages/customer/CustomerPage')),
  },
  {
    path: '/simulator',
    label: 'Retention simulator',
    group: 'Act',
    icon: 'simulator',
    page: lazy(() => import('../pages/simulator/SimulatorPage')),
  },
  {
    path: '/batch',
    label: 'Batch scoring',
    group: 'Act',
    icon: 'batch',
    page: lazy(() => import('../pages/batch/BatchPage')),
  },
];

export const GROUPS = ['Book', 'Model', 'Act'] as const;

export function routeFor(pathname: string): Route | undefined {
  return ROUTES.find((route) => route.path === pathname);
}
