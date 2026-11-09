/** The eight pages, by address and by the name the sidebar gives them (src/app/routes.ts). */
export const PAGES = [
  { path: '/', label: 'Executive overview' },
  { path: '/explorer', label: 'Customer explorer' },
  { path: '/patterns', label: 'Churn patterns' },
  { path: '/performance', label: 'Model performance' },
  { path: '/explainability', label: 'Explainability' },
  { path: '/customer', label: 'Single customer' },
  { path: '/simulator', label: 'Retention simulator' },
  { path: '/batch', label: 'Batch scoring' },
] as const;
