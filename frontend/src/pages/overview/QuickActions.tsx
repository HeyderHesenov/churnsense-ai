/** Quick actions: links to the pages that do each thing. Nothing here acts on its own. */
import type { ComponentType } from 'react';
import { Link } from '../../app/router';
import { icons } from '../../components/icons';
import { Card, CardHeader } from '../../components/ui/Surface';
import styles from './QuickActions.module.css';

interface Action {
  to: string;
  title: string;
  sub: string;
  tone: 'accent' | 'amber' | 'violet' | 'pink';
  Icon: ComponentType<{ size?: number }>;
}

export const QUICK_ACTIONS: readonly Action[] = [
  {
    to: '/explorer',
    title: 'Find a customer',
    sub: 'Search and filter every scored customer',
    tone: 'accent',
    Icon: icons.search,
  },
  {
    to: '/simulator',
    title: 'Plan a campaign',
    sub: 'Set assumptions and choose a cut-off',
    tone: 'amber',
    Icon: icons.simulator,
  },
  {
    to: '/batch',
    title: 'Score a file',
    sub: 'Upload a CSV file to the server',
    tone: 'violet',
    Icon: icons.upload,
  },
  {
    to: '/performance',
    title: 'Check the model',
    sub: 'Test results and calibration',
    tone: 'pink',
    Icon: icons.performance,
  },
];

export function QuickActions({ className }: { className?: string | undefined }) {
  return (
    <Card titleId="quick" className={className}>
      <CardHeader id="quick" title="Quick actions" />
      <ul className={styles.list}>
        {QUICK_ACTIONS.map(({ to, title, sub, tone, Icon }) => (
          <li key={to}>
            <Link to={to} className={styles.action}>
              <span className={styles.chip} data-tone={tone} aria-hidden="true">
                <Icon size={15} />
              </span>
              <span className={styles.text}>
                <span className={styles.title}>{title}</span>{' '}
                <span className={styles.sub}>{sub}</span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}
