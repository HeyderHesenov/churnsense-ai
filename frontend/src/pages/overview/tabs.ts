/**
 * The overview's four tabs, and the one address they share with the retention
 * queue: ``?tab=`` names the tab (the summary when absent), and the queue's
 * filters stay in the address whichever tab is shown, so moving between tabs
 * never loses them and Back steps through both.
 */
import { pageTabs } from '../../components/ui/PageTabs';
import { QUEUE_PARAMS } from './queue';

export const OVERVIEW_TABS = pageTabs([
  { value: 'summary', label: 'Summary', hint: 'The book at a glance' },
  { value: 'distribution', label: 'Risk distribution', hint: 'Where customers sit' },
  { value: 'associations', label: 'Associations', hint: 'What risk goes with' },
  { value: 'queue', label: 'Retention queue', hint: 'Who to contact first' },
]);

export type OverviewTab = (typeof OVERVIEW_TABS.tabs)[number]['value'];

/** The tab and the queue's filters: one schema, so one change is one history entry. */
export const OVERVIEW_PARAMS = { ...QUEUE_PARAMS, ...OVERVIEW_TABS.schema };
