/**
 * The performance page's four tabs and the address they share. ``?tab=``
 * names the tab (the summary when absent), ``?part=`` the partition the
 * summary and the errors tab read (validation when absent), and ``?cut=`` the
 * explored validation cut. One schema, so one change is one history entry and
 * Back steps through tabs, partitions and cuts alike.
 *
 * There are two partitions to choose, never three: training chose the model
 * and has no figures of its own here.
 */
import { param } from '../../app/params';
import { pageTabs } from '../../components/ui/PageTabs';

export const PERFORMANCE_TABS = pageTabs([
  { value: 'summary', label: 'Summary', hint: 'The headline figures' },
  { value: 'choice', label: 'Model choice', hint: 'Why this model' },
  { value: 'errors', label: 'Errors & trade-offs', hint: 'What each cut-off costs' },
  { value: 'calibration', label: 'Calibration & blind spots', hint: 'Can you trust a score' },
]);

export type PerformanceTab = (typeof PERFORMANCE_TABS.tabs)[number]['value'];

export const PARTITIONS = ['validation', 'test'] as const;
export type Partition = (typeof PARTITIONS)[number];

export const PERFORMANCE_PARAMS = {
  ...PERFORMANCE_TABS.schema,
  /** The partition the summary and the errors tab show. */
  part: param.choice(PARTITIONS, 'validation'),
  /** The explored cut as ``cut`` writes it ("0.40"); empty at the served model's own. */
  cut: param.text({ pattern: /^(0\.\d\d|1\.00)$/, maxLength: 4 }),
};
