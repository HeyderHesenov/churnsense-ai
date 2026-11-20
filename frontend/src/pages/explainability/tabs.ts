/**
 * The explainability page's two tabs and the address they share. ``?tab=``
 * names the tab (the global attributions when absent); ``?f=`` and ``?id=``
 * keep their meaning on both. One schema, so a tab change is one history
 * entry and Back steps through tabs, attributes and customers alike.
 */
import { pageTabs } from '../../components/ui/PageTabs';
import { EXPLAIN_PARAMS } from './explain';

export const EXPLAIN_TABS = pageTabs([
  {
    value: 'global',
    label: 'Global attributions',
    hint: 'How the model uses each attribute',
  },
  {
    value: 'rankings',
    label: 'Two rankings',
    hint: 'Association beside the model’s own order',
  },
]);

export type ExplainTab = (typeof EXPLAIN_TABS.tabs)[number]['value'];

export const EXPLAIN_PAGE_PARAMS = { ...EXPLAIN_TABS.schema, ...EXPLAIN_PARAMS };
