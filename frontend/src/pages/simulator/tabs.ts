/**
 * The simulator's three tabs, and the address they share with the
 * assumptions and the cut. ``?tab=`` names the tab (Simulate when absent); a
 * tab change is one history entry, so Back returns to the tab before, with
 * the assumptions and the cut as they were.
 */
import { pageTabs } from '../../components/ui/PageTabs';
import { SIMULATOR_PARAMS } from './simulator';

export const SIMULATOR_TABS = pageTabs([
  { value: 'simulate', label: 'Simulate', hint: 'Set assumptions, read the result' },
  { value: 'compare', label: 'Compare', hint: 'Served, recommended, selected, pinned' },
  { value: 'method', label: 'Method', hint: 'Why this cut-off' },
]);

export type SimulatorTab = (typeof SIMULATOR_TABS.tabs)[number]['value'];

export const SIMULATOR_PAGE_PARAMS = { ...SIMULATOR_TABS.schema, ...SIMULATOR_PARAMS };
