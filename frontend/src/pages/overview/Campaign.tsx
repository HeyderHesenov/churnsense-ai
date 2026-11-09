/**
 * "Campaign at 0.36": the simulated money at the shipped threshold, on the
 * validation partition, with every assumption it rests on. The sparkline is
 * the validation sweep the API returns; the page draws it and computes
 * nothing but the gap between two of its figures.
 */
import { scaleLinear } from 'd3-scale';
import type { Overview } from '../../api/queries';
import { ChartFrame } from '../../components/charts/ChartFrame';
import { icons } from '../../components/icons';
import { ButtonLink } from '../../components/ui/Button';
import { Card, CardHeader, SimulatedBadge } from '../../components/ui/Surface';
import { cut, int, money, probability } from '../../lib/format';
import { assumptionsNote, campaignCaption, campaignLabel } from './copy';
import styles from './Campaign.module.css';

type Campaign = NonNullable<Overview['campaign']>;

const WIDTH = 260;
const HEIGHT = 64;

export function CampaignCard({ campaign }: { campaign: Campaign | null }) {
  if (!campaign) {
    return (
      <Card titleId="campaign" variant="sim" className={styles.card}>
        <CardHeader id="campaign" title="Campaign" aside={<SimulatedBadge />} />
        <p className={styles.note}>
          The simulated campaign needs observed outcomes on the validation partition, which are
          withheld while the partitions cannot be verified.
        </p>
      </Card>
    );
  }
  return (
    <Card titleId="campaign" variant="sim" className={styles.card}>
      <CardHeader
        id="campaign"
        title={`Campaign at ${cut(campaign.threshold)}`}
        aside={<SimulatedBadge />}
      />
      <div>
        <div className={styles.label}>Net benefit on validation</div>
        <div className={styles.net}>{money(campaign.net_benefit)}</div>
      </div>
      <Sparkline campaign={campaign} />
      <dl className={styles.stats}>
        <div>
          <dt>Flagged</dt>
          <dd>
            <span>{int(campaign.flagged)}</span>{' '}
            <span className={styles.of}>/ {int(campaign.customers)}</span>
          </dd>
        </div>
        <div>
          <dt>Precision · recall</dt>
          <dd>
            {probability(campaign.precision)} · {probability(campaign.recall)}
          </dd>
        </div>
        <div>
          <dt>Campaign cost</dt>
          <dd>{money(campaign.intervention_cost)}</dd>
        </div>
        <div>
          <dt>Retained value</dt>
          <dd>{money(campaign.retained_value)}</dd>
        </div>
      </dl>
      <p className={styles.assumed}>{assumptionsNote(campaign.assumptions)}</p>
      <ButtonLink to="/simulator" variant="sim" size="lg" className={styles.open}>
        Open simulator
        <icons.arrowRight size={15} />
      </ButtonLink>
    </Card>
  );
}

function Sparkline({ campaign }: { campaign: Campaign }) {
  const values = campaign.curve.map((point) => point.net_benefit);
  const x = scaleLinear().domain([0, 1]).range([0, WIDTH]);
  const y = scaleLinear()
    .domain([Math.min(...values), Math.max(...values)])
    .range([60, 6]);
  const path = campaign.curve
    .map(
      (point, i) =>
        `${i === 0 ? 'M' : 'L'}${x(point.threshold).toFixed(1)} ${y(point.net_benefit).toFixed(1)}`,
    )
    .join(' ');
  const [low, high] = y.domain() as [number, number];
  const zero = low <= 0 && high >= 0 ? y(0) : null;
  return (
    <ChartFrame
      className={styles.figure}
      label={campaignLabel(campaign)}
      table={{
        caption: 'Simulated net benefit on validation',
        columns: ['Point', 'Threshold', 'Net benefit'],
        rows: [
          ['Shipped', cut(campaign.threshold), money(campaign.net_benefit)],
          ['Peak', cut(campaign.peak_threshold), money(campaign.peak_net_benefit)],
        ],
      }}
      legend={
        <span className={styles.caption}>
          <span>
            <span className={styles.shippedDot} aria-hidden="true">
              ●{' '}
            </span>
            <span>{campaignCaption(campaign)}</span>
          </span>
          <span className={styles.axisNote}>threshold 0 → 1</span>
        </span>
      }
    >
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`}>
        {zero !== null && <line className={styles.zero} x1={0} x2={WIDTH} y1={zero} y2={zero} />}
        <line
          className={styles.shippedLine}
          x1={x(campaign.threshold)}
          x2={x(campaign.threshold)}
          y1={0}
          y2={60}
        />
        <path className={styles.curve} d={path} />
        <circle
          className={styles.peak}
          cx={x(campaign.peak_threshold)}
          cy={y(campaign.peak_net_benefit)}
          r={3.5}
        />
        <circle
          className={styles.shipped}
          cx={x(campaign.threshold)}
          cy={y(campaign.net_benefit)}
          r={4}
        />
      </svg>
    </ChartFrame>
  );
}
