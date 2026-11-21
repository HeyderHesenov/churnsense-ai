/**
 * The test record in plain words: three sentences, each made of the record's
 * own counts. No "N in 10", no ratio and no lift: a sentence states cells of
 * the two-by-two table and, where the record has one, the rate it publishes.
 * Each bar draws two of the cells side by side; it shows no figure of its own.
 */
import { Card, CardHeader, Tag } from '../../components/ui/Surface';
import { cx } from '../../lib/style';
import { plainCaught, plainMissed, plainOffers } from './copy';
import { precisionOf, recallOf, type Outcome, type Recorded } from './performance';
import styles from './Performance.module.css';

/** Two cells of the table on one bar, each as wide as its count. */
function Split({
  first,
  second,
  lit,
}: {
  first: [Outcome, number];
  second: [Outcome, number];
  lit: Outcome;
}) {
  const total = first[1] + second[1];
  // Two empty cells have nothing to draw: no bar, rather than one that looks full.
  if (total <= 0) return null;
  const width = (first[1] / total) * 100;
  return (
    <svg className={styles.split} viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">
      <rect
        data-outcome={first[0]}
        className={cx(lit !== first[0] && styles.dim)}
        x={0}
        y={0}
        width={width}
        height={8}
      />
      <rect
        data-outcome={second[0]}
        className={cx(lit !== second[0] && styles.dim)}
        x={width}
        y={0}
        width={100 - width}
        height={8}
      />
    </svg>
  );
}

export function PlainCards({ record }: { record: Recorded }) {
  const m = record.metrics;
  const items = [
    {
      key: 'caught',
      title: 'Churners flagged',
      text: plainCaught(m.tp, m.positives),
      rate: `recall ${recallOf(m.positives, m.recall)}`,
      bar: <Split first={['tp', m.tp]} second={['fn', m.fn]} lit="tp" />,
    },
    {
      key: 'offers',
      title: 'Who the flags reach',
      text: plainOffers(m.tp, m.fp),
      rate: `precision ${precisionOf(m.tp + m.fp, m.precision)}`,
      bar: <Split first={['tp', m.tp]} second={['fp', m.fp]} lit="tp" />,
    },
    {
      key: 'missed',
      title: 'Churners missed',
      text: plainMissed(m.fn, m.threshold),
      rate: null,
      bar: <Split first={['tp', m.tp]} second={['fn', m.fn]} lit="fn" />,
    },
  ];
  return (
    <Card titleId="plain-title" className={styles.card}>
      <CardHeader id="plain-title" title="In plain words" aside={<Tag>TEST</Tag>} />
      <ul className={styles.plainList}>
        {items.map((item) => (
          <li key={item.key} className={styles.plainItem}>
            <h3 className={styles.plainTitle}>{item.title}</h3>
            <p className={styles.plainText}>{item.text}</p>
            {item.bar}
            {item.rate && <p className={styles.plainRate}>{item.rate}</p>}
          </li>
        ))}
      </ul>
    </Card>
  );
}
