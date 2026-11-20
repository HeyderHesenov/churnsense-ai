/**
 * The eight pages, by address and by the name the sidebar gives them, with
 * the breadcrumb's section and the sidebar's group (src/app/routes.ts). The
 * two differ for the single customer: about the model, listed to act.
 */
export const PAGES = [
  { path: '/', label: 'Executive overview', section: 'Book', navGroup: 'Understand' },
  { path: '/explorer', label: 'Customer explorer', section: 'Book', navGroup: 'Understand' },
  { path: '/patterns', label: 'Churn patterns', section: 'Book', navGroup: 'Understand' },
  {
    path: '/performance',
    label: 'Model performance',
    section: 'Model',
    navGroup: 'Trust the model',
  },
  {
    path: '/explainability',
    label: 'Explainability',
    section: 'Model',
    navGroup: 'Trust the model',
  },
  { path: '/customer', label: 'Single customer', section: 'Model', navGroup: 'Take action' },
  { path: '/simulator', label: 'Retention simulator', section: 'Act', navGroup: 'Take action' },
  { path: '/batch', label: 'Batch scoring', section: 'Act', navGroup: 'Take action' },
] as const;

/**
 * The overview's tabs other than the summary (src/pages/overview/tabs.ts), each
 * at its own address: checked like a page wherever every page is.
 */
export const OVERVIEW_TABS = [
  { path: '/?tab=distribution', label: 'Overview risk distribution' },
  { path: '/?tab=associations', label: 'Overview associations' },
  { path: '/?tab=queue', label: 'Overview retention queue' },
] as const;

/**
 * The performance page's tabs other than the summary, and its test partition
 * (src/pages/performance/tabs.ts), each at its own address: checked like a page.
 */
export const PERFORMANCE_TABS = [
  { path: '/performance?part=test', label: 'Performance summary test' },
  { path: '/performance?tab=choice', label: 'Performance model choice' },
  { path: '/performance?tab=errors', label: 'Performance errors validation' },
  { path: '/performance?tab=errors&part=test', label: 'Performance errors test' },
  { path: '/performance?tab=calibration', label: 'Performance calibration' },
] as const;
