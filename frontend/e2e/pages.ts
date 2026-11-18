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
