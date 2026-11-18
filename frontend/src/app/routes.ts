/**
 * The eight pages, in the sidebar's order.
 *
 * A page has two places, on purpose. ``group`` is the breadcrumb's section:
 * what the page is about (the book, the model, an action). ``navGroup`` is the
 * sidebar's: what a reader goes there to do. They differ for one page: the
 * single customer is scored and explained by the model (breadcrumb: Model),
 * and it is where a reader acts on one customer (sidebar: Take action).
 */
import { lazy, type ComponentType, type LazyExoticComponent } from 'react';
import type { icons } from '../components/icons';

export const NAV_GROUPS = ['Understand', 'Trust the model', 'Take action'] as const;

export interface Route {
  path: string;
  label: string;
  /** The sidebar's second line. */
  description: string;
  /** The breadcrumb's section. */
  group: 'Book' | 'Model' | 'Act';
  /** The sidebar group the page is listed under. */
  navGroup: (typeof NAV_GROUPS)[number];
  icon: keyof typeof icons;
  page: LazyExoticComponent<ComponentType>;
}

export const ROUTES: readonly Route[] = [
  {
    path: '/',
    label: 'Executive overview',
    description: 'Where risk sits today',
    group: 'Book',
    navGroup: 'Understand',
    icon: 'overview',
    page: lazy(() => import('../pages/overview/OverviewPage')),
  },
  {
    path: '/explorer',
    label: 'Customer explorer',
    description: 'Every customer, filterable',
    group: 'Book',
    navGroup: 'Understand',
    icon: 'explorer',
    page: lazy(() => import('../pages/explorer/ExplorerPage')),
  },
  {
    path: '/patterns',
    label: 'Churn patterns',
    description: 'Which groups left',
    group: 'Book',
    navGroup: 'Understand',
    icon: 'patterns',
    page: lazy(() => import('../pages/patterns/PatternsPage')),
  },
  {
    path: '/performance',
    label: 'Model performance',
    description: 'How good, where blind',
    group: 'Model',
    navGroup: 'Trust the model',
    icon: 'performance',
    page: lazy(() => import('../pages/performance/PerformancePage')),
  },
  {
    path: '/explainability',
    label: 'Explainability',
    description: 'What moves the score',
    group: 'Model',
    navGroup: 'Trust the model',
    icon: 'explainability',
    page: lazy(() => import('../pages/explainability/ExplainabilityPage')),
  },
  {
    path: '/customer',
    label: 'Single customer',
    description: 'Score and explain one',
    group: 'Model',
    navGroup: 'Take action',
    icon: 'customer',
    page: lazy(() => import('../pages/customer/CustomerPage')),
  },
  {
    path: '/simulator',
    label: 'Retention simulator',
    description: 'Choose the cut-off',
    group: 'Act',
    navGroup: 'Take action',
    icon: 'simulator',
    page: lazy(() => import('../pages/simulator/SimulatorPage')),
  },
  {
    path: '/batch',
    label: 'Batch scoring',
    description: 'Score a CSV file',
    group: 'Act',
    navGroup: 'Take action',
    icon: 'batch',
    page: lazy(() => import('../pages/batch/BatchPage')),
  },
];

export function routeFor(pathname: string): Route | undefined {
  return ROUTES.find((route) => route.path === pathname);
}
