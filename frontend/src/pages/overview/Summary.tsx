/**
 * The top of the overview (what the page is, and the operating point on the
 * band scale) and its summary tab: the five key figures, then the bento -
 * risk at a glance, the briefing, the simulated campaign, the customers to
 * call first, the segments to watch, churn by tenure and quick actions.
 * Every figure is the API's; the page formats and places them.
 */
import type { ModelInfo, Overview } from '../../api/queries';
import { icons } from '../../components/icons';
import { BandSwatch } from '../../components/ui/Badges';
import { ButtonLink } from '../../components/ui/Button';
import { CountUp } from '../../components/ui/CountUp';
import { PageHeader } from '../../components/ui/PageHeader';
import { Card, StatTile } from '../../components/ui/Surface';
import { bandTop } from '../../lib/bands';
import { cut, int, money, pct } from '../../lib/format';
import { Briefing } from './Briefing';
import { CallFirst } from './CallFirst';
import { CampaignCard } from './Campaign';
import { scaleDescription, scaleLabel, SHOW_IN_QUEUE } from './copy';
import { BandRates, ChargeShares, MiniHistogram, ShareRing } from './KpiVisuals';
import { trackPercent } from './Meter';
import { QuickActions } from './QuickActions';
import { RiskDonut } from './RiskDonut';
import { TenureLine } from './TenureLine';
import { WatchSegments } from './WatchSegments';
import bento from './Bento.module.css';
import styles from './Overview.module.css';

export function Intro({ book, info }: { book: Overview; info: ModelInfo | undefined }) {
  return (
    <section className={styles.intro} aria-label="Overview">
      <PageHeader
        eyebrow="Executive overview"
        title="Where retention risk sits in the book"
        className={styles.title}
      >
        {int(book.customers)} customers, scored by one {info?.calibrated ? 'calibrated ' : ''}
        model through the same path the API uses.{' '}
        {book.withheld_reason
          ? `Observed outcomes are withheld: ${book.withheld_reason}`
          : 'Observed outcomes come from the train and validation partitions only; test outcomes stay withheld.'}
      </PageHeader>
      <OperatingPoint book={book} />
    </section>
  );
}

/**
 * Where the threshold's chip is anchored: centred, or held inside the scale
 * near either end so it is never cut off. Drawing only.
 */
export function chipAnchor(threshold: number): 'start' | 'middle' | 'end' {
  return threshold < 0.12 ? 'start' : threshold > 0.88 ? 'end' : 'middle';
}

/** The chip's box, in pixels from the threshold: as wide as "flag ≥ 0.00" in the mono face. */
const CHIP = { width: 86, height: 18 };

function OperatingPoint({ book }: { book: Overview }) {
  const threshold = cut(book.threshold);
  const anchor = chipAnchor(book.threshold);
  const chipX = anchor === 'start' ? -6 : anchor === 'end' ? 6 - CHIP.width : -CHIP.width / 2;
  return (
    <Card titleId="operating-point" className={styles.operating}>
      <div className={styles.operatingHead}>
        <div>
          <h2 id="operating-point" className={styles.operatingLabel}>
            Operating point
          </h2>
          <div className={styles.operatingValue}>
            <span className={styles.threshold}>{threshold}</span>
            <span className={styles.muted}>chosen on validation, stored with the artifact</span>
          </div>
        </div>
        <ButtonLink to="/simulator" variant="ghost" size="md">
          Tune
          <icons.arrowRight size={14} />
        </ButtonLink>
      </div>
      <div
        className={styles.scale}
        role="img"
        aria-label={scaleDescription(book.bands, book.threshold)}
      >
        {/* No viewBox: percentages place the marks, and the chip keeps its pixel size. */}
        <svg className={styles.scaleSvg} aria-hidden="true" focusable="false">
          <svg y={26} height={12} width="100%">
            {book.bands.map((band) => (
              <rect
                key={band.name}
                data-band={band.name}
                className={styles.segment}
                x={trackPercent(band.min)}
                width={trackPercent(bandTop(band) - band.min)}
                height={12}
                rx={3}
              />
            ))}
            {book.bands.slice(1).map((band) => (
              <line
                key={band.name}
                className={styles.gap}
                x1={trackPercent(band.min)}
                x2={trackPercent(band.min)}
                y1={0}
                y2={12}
              />
            ))}
          </svg>
          <svg x={trackPercent(book.threshold)} overflow="visible">
            <rect
              className={styles.chip}
              x={chipX}
              y={0}
              width={CHIP.width}
              height={CHIP.height}
              rx={5}
            />
            <text className={styles.chipText} x={chipX + CHIP.width / 2} y={13} textAnchor="middle">
              flag ≥ {threshold}
            </text>
            <line className={styles.stem} x1={0} x2={0} y1={CHIP.height} y2={42} />
          </svg>
        </svg>
        <ul className={styles.legend}>
          {book.bands.map((band, i) => (
            <li key={band.name}>
              <BandSwatch band={band.name} />
              {band.name} <span className={styles.range}>{scaleLabel(band, i)}</span>
            </li>
          ))}
        </ul>
      </div>
      <p className={styles.note}>
        Bands are fixed ranges, independent of the threshold: retuning it never redefines what the
        team calls Critical.
      </p>
    </Card>
  );
}

interface KeyFiguresProps {
  book: Overview;
  /** Addresses of the queue tab: on the riskiest bands, and on every flagged band. */
  links: { riskiest: string; flagged: string };
  /** Following a tile: the page then focuses the queue. */
  onQueue: () => void;
}

function KeyFigures({ book, links, onQueue }: KeyFiguresProps) {
  const threshold = cut(book.threshold);
  const share = (segment: Overview['flagged']) =>
    `${int(segment.in_queue_active)} active, ${int(segment.in_queue_unknown)} status unknown · ${money(segment.in_queue_monthly)} / mo active`;
  const observed = book.observed;
  const withheld = observed?.rate == null;
  return (
    <section aria-label="Key figures" className={styles.figures}>
      <StatTile
        label="Customers scored"
        value={<CountUp value={book.customers} format={int} />}
        sub="every row, one prediction path"
        visual={<MiniHistogram bins={book.histogram} />}
      />
      <StatTile
        label="Observed churn rate"
        value={withheld ? 'Withheld' : <CountUp value={observed.rate} format={pct} />}
        sub={
          withheld || observed.churned === null ? (
            'outcomes not shown'
          ) : (
            <>
              {int(observed.churned)} of {int(observed.customers)} ·{' '}
              <span className={styles.partitionTag}>TRAIN+VAL</span>
            </>
          )
        }
        visual={
          withheld || book.withheld_reason !== null ? undefined : <BandRates bands={book.bands} />
        }
      />
      <StatTile
        label="Monthly charges"
        value={
          book.current_monthly_charges === null ? (
            'Unavailable'
          ) : (
            <CountUp value={book.current_monthly_charges} format={money} />
          )
        }
        sub={
          book.unknown_customers === 0
            ? 'customers recorded active'
            : `customers recorded active; ${int(book.unknown_customers)} with unknown status (${money(book.unknown_monthly_charges)}) not counted`
        }
        visual={
          book.bands.every((band) => band.monthly_share !== null) ? (
            <ChargeShares bands={book.bands} />
          ) : undefined
        }
      />
      <StatTile
        label="High or Critical, in the queue"
        value={<CountUp value={book.high_or_critical.in_queue} format={int} />}
        sub={share(book.high_or_critical)}
        link={{ to: links.riskiest, description: SHOW_IN_QUEUE, onFollow: onQueue }}
        visual={
          book.high_or_critical.in_queue_share === null ? undefined : (
            <ShareRing share={book.high_or_critical.in_queue_share} tone="violet" />
          )
        }
      />
      <StatTile
        label={`Flagged at ${threshold}, in the queue`}
        value={<CountUp value={book.flagged.in_queue} format={int} />}
        sub={share(book.flagged)}
        accent
        link={{ to: links.flagged, description: SHOW_IN_QUEUE, onFollow: onQueue }}
        visual={
          book.flagged.in_queue_share === null ? undefined : (
            <ShareRing share={book.flagged.in_queue_share} tone="accent" />
          )
        }
      />
    </section>
  );
}

interface SummaryTabProps {
  book: Overview;
  /** Addresses of the other tabs: the queue (as it opens, and on the riskiest bands) and the distribution. */
  links: { riskiest: string; flagged: string; queue: string; distribution: string };
  onQueue: () => void;
}

export function SummaryTab({ book, links, onQueue }: SummaryTabProps) {
  return (
    <div className={bento.tab}>
      <KeyFigures book={book} links={links} onQueue={onQueue} />
      <div className={bento.grid}>
        <RiskDonut book={book} more={links.distribution} className={bento.glance} />
        <Briefing
          book={book}
          links={{ queue: links.queue }}
          onQueue={onQueue}
          className={bento.briefing}
        />
        <CampaignCard campaign={book.campaign} compact className={bento.campaign} />
        <CallFirst book={book} queue={links.queue} onQueue={onQueue} className={bento.callFirst} />
        <WatchSegments withheld={book.withheld_reason} className={bento.watch} />
        <TenureLine
          lifecycle={book.lifecycle}
          withheld={book.withheld_reason}
          className={bento.tenure}
        />
        <QuickActions className={bento.quick} />
      </div>
    </div>
  );
}
