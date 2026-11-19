/**
 * The briefing: four findings, each a figure the API returned, and a way to
 * the tab or page that shows more. A row whose figure the API does not give
 * is left out, never filled in.
 */
import type { ReactNode } from 'react';
import { usePerformance, type Overview } from '../../api/queries';
import { Link } from '../../app/router';
import { icons } from '../../components/icons';
import { Card, CardHeader, SimulatedBadge, Tag } from '../../components/ui/Surface';
import { TermTip } from '../../components/ui/TermTip';
import { campaignBrief, flaggedBrief, tenureBrief } from './copy';
import styles from './Briefing.module.css';

type Tone = 'accent' | 'pink' | 'violet' | 'amber';

interface BriefProps {
  icon: ReactNode;
  tone: Tone;
  title: ReactNode;
  children: ReactNode;
  to: string;
  /** What following the arrow opens, as its name. */
  go: string;
  onFollow?: () => void;
}

function Brief({ icon, tone, title, children, to, go, onFollow }: BriefProps) {
  return (
    <li className={styles.row}>
      <span className={styles.chip} data-tone={tone} aria-hidden="true">
        {icon}
      </span>
      <span className={styles.text}>
        <span className={styles.title}>{title}</span>
        <span className={styles.fact}>{children}</span>
      </span>
      <Link to={to} aria-label={go} className={styles.go} onClick={onFollow}>
        <icons.arrowRight size={15} />
      </Link>
    </li>
  );
}

interface BriefingProps {
  book: Overview;
  /** The retention queue tab's address. */
  links: { queue: string };
  /** Following the queue's link: the page then brings the queue's heading into focus. */
  onQueue: () => void;
  className?: string | undefined;
}

export function Briefing({ book, links, onQueue, className }: BriefingProps) {
  const performance = usePerformance();
  const ece = performance.data?.validation_evaluation?.ece;
  const flagged = flaggedBrief(book);
  const tenure = tenureBrief(book.lifecycle);
  const campaign = book.campaign ? campaignBrief(book.campaign) : null;
  return (
    <Card titleId="briefing" className={className}>
      <CardHeader id="briefing" title="Briefing" subtitle="What the figures say today" />
      <ul className={styles.list}>
        <Brief
          icon={<icons.flag size={16} />}
          tone="accent"
          title={flagged.title}
          to={links.queue}
          go="Open the retention queue"
          onFollow={onQueue}
        >
          {flagged.fact}
        </Brief>
        {tenure && (
          <Brief
            icon={<icons.patterns size={16} />}
            tone="pink"
            title={tenure.title}
            to="/patterns"
            go="Open churn patterns"
          >
            {tenure.fact}
          </Brief>
        )}
        {ece !== undefined && (
          <Brief
            icon={<icons.performance size={16} />}
            tone="violet"
            title={
              <>
                Calibration on validation <Tag>VALIDATION</Tag>
              </>
            }
            to="/performance"
            go="Open model performance"
          >
            Expected calibration error {ece.toFixed(3)}{' '}
            <TermTip term="ECE">
              Expected calibration error: how far stated probabilities sit from the churn observed
              among customers given them, averaged over the score range. Lower is closer.
            </TermTip>
          </Brief>
        )}
        {campaign && (
          <Brief
            icon={<icons.simulator size={16} />}
            tone="amber"
            title={
              <>
                {campaign.title} <SimulatedBadge />
              </>
            }
            to="/simulator"
            go="Open the retention simulator"
          >
            {campaign.fact}
          </Brief>
        )}
      </ul>
    </Card>
  );
}
