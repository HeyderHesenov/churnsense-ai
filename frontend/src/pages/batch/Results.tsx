/**
 * An accepted file's results: the API's summary in tiles, then its rows a
 * page at a time. The order, the flagged-only view and the page are the
 * reader's, kept while the page shows this file; the figures are the API's,
 * and "Scored CSV" saves the file the API built from the same scoring.
 */
import { useMemo, useState } from 'react';
import { flushSync } from 'react-dom';
import type { BatchScore } from '../../api/queries';
import { icons } from '../../components/icons';
import { BandLabel, ProbabilityMeter } from '../../components/ui/Badges';
import { Button } from '../../components/ui/Button';
import { Pagination } from '../../components/ui/Pagination';
import { nextSort, SortHeader } from '../../components/ui/SortHeader';
import { EmptyState } from '../../components/ui/States';
import { Card, CardHeader, StatTile, Tag } from '../../components/ui/Surface';
import type { Option } from '../../components/ui/Toggle';
import { Segmented } from '../../components/ui/Toggle';
import { cents, cut, int, money, pct, probability } from '../../lib/format';
import { profile } from '../../lib/labels';
import { cssVars, cx } from '../../lib/style';
import {
  arrange,
  DEFAULT_DIRECTION,
  DEFAULT_SORT,
  PAGE_SIZES,
  type BatchRow,
  type RowSort,
  type RowSortKey,
  type Show,
  type Stage,
} from './batch';
import { labelTile, pageNote, ROWS_NOTE, scoredMeta } from './copy';
import styles from './Batch.module.css';

const ROWS_ID = 'scored-rows';

const SHOW: readonly Option<Show>[] = [
  { value: 'all', label: 'All rows' },
  { value: 'flagged', label: 'Flagged' },
];

interface ResultsProps {
  stage: Extract<Stage, { kind: 'accepted' }>;
  onSave: (name: string, text: string) => void;
}

export function Results({ stage, onSave }: ResultsProps) {
  const { result } = stage;
  const [show, setShow] = useState<Show>('all');
  const [sort, setSort] = useState<RowSort>(DEFAULT_SORT);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState<number>(PAGE_SIZES[0]);
  const arranged = useMemo(() => arrange(result.rows, show, sort), [result.rows, show, sort]);
  const pages = Math.max(1, Math.ceil(arranged.length / size));
  const first = (page - 1) * size;
  const shown = arranged.slice(first, first + size);

  const showing = (next: Show) => {
    setShow(next);
    setPage(1);
  };
  // The empty state and its button go: the focus goes to the rows' heading.
  const showAll = () => {
    flushSync(() => showing('all'));
    document.getElementById(ROWS_ID)?.focus();
  };

  return (
    <>
      <Tiles result={result} />
      <Card titleId={ROWS_ID} variant="flush">
        <div className={styles.rowsHead}>
          <CardHeader
            id={ROWS_ID}
            focusable
            title="Scored rows"
            subtitle={
              <>
                {scoredMeta(stage.name, stage.file.size, stage.at)}
                {stage.demo && (
                  <>
                    {' '}
                    <Tag>SYNTHETIC DEMO FILE</Tag>
                  </>
                )}
                <br />
                {ROWS_NOTE}
              </>
            }
            aside={
              <div className={styles.controls}>
                <Segmented
                  label="Rows shown"
                  options={SHOW}
                  value={show}
                  onChange={showing}
                  small
                />
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => onSave('churnsense_batch_predictions.csv', result.csv)}
                >
                  <icons.download size={14} />
                  Scored CSV
                </Button>
              </div>
            }
          />
        </div>
        {arranged.length === 0 ? (
          <EmptyState
            title="No row is flagged"
            action={{ label: 'Show all rows', onClick: showAll }}
          >
            Nothing in this file reaches the {cut(result.threshold)} threshold.
          </EmptyState>
        ) : (
          <>
            <Table
              rows={shown}
              sort={sort}
              onSort={(column) => {
                setSort(nextSort(sort, column, DEFAULT_DIRECTION));
                setPage(1);
              }}
              labelled={result.summary.labels !== null}
            />
            <Pagination
              page={page}
              pages={pages}
              onPage={setPage}
              note={pageNote(first + 1, shown.length, arranged.length, show)}
              pageSize={{
                value: size,
                options: PAGE_SIZES,
                onChange: (next) => {
                  // The first row shown stays on the page shown.
                  setPage(Math.floor(first / next) + 1);
                  setSize(next);
                },
              }}
            />
          </>
        )}
      </Card>
    </>
  );
}

function Tiles({ result }: { result: BatchScore }) {
  const { summary } = result;
  const labels = labelTile(summary);
  return (
    <section aria-label="Results summary" className={styles.tiles}>
      <StatTile
        compact
        label="Rows scored"
        value={int(summary.rows)}
        sub="every row, blank lines skipped"
      />
      <StatTile
        compact
        label={`Flagged at ${cut(result.threshold)}`}
        value={int(summary.flagged)}
        sub={summary.flagged_share === null ? '—' : `${pct(summary.flagged_share)} of the file`}
      />
      <StatTile
        compact
        label="Mean probability"
        value={summary.mean_probability === null ? '—' : probability(summary.mean_probability)}
        sub={`expected churners ${int(summary.expected_churners)}`}
      />
      <StatTile
        compact
        label="Monthly charges at risk"
        value={`${money(summary.at_risk)} / mo`}
        sub="Expected: Σ probability × monthly charges, assuming each row is a current customer"
      />
      <div className={styles.bandsTile}>
        <span className={styles.tileLabel}>By band</span>
        <span className={styles.bandBar} aria-hidden="true">
          {summary.bands.map((band) => (
            <span
              key={band.name}
              data-band={band.name}
              className={styles.bandPart}
              style={cssVars({ '--share': summary.rows ? band.customers / summary.rows : 0 })}
            />
          ))}
        </span>
        <ul aria-label="Rows by band" className={styles.bandList}>
          {summary.bands.map((band) => (
            <li key={band.name}>
              <BandLabel band={band.name} inked /> {int(band.customers)}
            </li>
          ))}
        </ul>
      </div>
      {labels && (
        <StatTile
          compact
          label="On this file’s own labels"
          value={<span className={styles.rates}>{labels.value}</span>}
          sub={labels.sub}
        />
      )}
    </section>
  );
}

/** "Month-to-month · Fiber · E-check · 3 mo". */
function profileOf({ features }: BatchRow): string {
  const text = (column: string) => String(features[column] ?? '');
  return `${profile(text('Contract'), text('InternetService'), text('PaymentMethod'))} · ${text('tenure')} mo`;
}

interface TableProps {
  rows: readonly BatchRow[];
  sort: RowSort;
  onSort: (column: RowSortKey) => void;
  /** The file's labels were compared: a held-out customer's reads "withheld". */
  labelled: boolean;
}

function Table({ rows, sort, onSort, labelled }: TableProps) {
  return (
    <div className={styles.scroll}>
      <div role="table" aria-label="Scored rows" className={styles.table} data-labelled={labelled}>
        <div role="rowgroup">
          <div role="row" className={cx(styles.row, styles.head)}>
            <SortHeader column="row" label="Row" sort={sort} onSort={onSort} />
            <SortHeader column="customer" label="Customer" sort={sort} onSort={onSort} />
            <SortHeader column="probability" label="Probability" sort={sort} onSort={onSort} />
            <div role="columnheader" className={styles.headCell}>
              Band
            </div>
            <div role="columnheader" className={styles.headCell}>
              Profile
            </div>
            <SortHeader column="monthly" label="Monthly" sort={sort} onSort={onSort} align="end" />
            {labelled && (
              <div role="columnheader" className={cx(styles.headCell, styles.end)}>
                Label
              </div>
            )}
          </div>
        </div>
        <div role="rowgroup">
          {rows.map((row) => (
            <div role="row" key={row.row} className={styles.row}>
              <div role="cell" className={cx(styles.cell, styles.position)}>
                {row.row}
              </div>
              <div role="cell" className={cx(styles.cell, styles.id)}>
                {row.customer_id ?? '—'}
              </div>
              <div role="cell" className={styles.cell}>
                <ProbabilityMeter value={row.churn_probability} band={row.risk_band} />
              </div>
              <div role="cell" className={styles.cell}>
                <BandLabel band={row.risk_band} inked />
              </div>
              <div role="cell" className={cx(styles.cell, styles.profile)}>
                {profileOf(row)}
              </div>
              <div role="cell" className={cx(styles.cell, styles.number)}>
                {cents(Number(row.features.MonthlyCharges))}
              </div>
              {labelled && (
                <div
                  role="cell"
                  className={cx(styles.cell, styles.number, styles.label)}
                  data-label={row.label ?? 'withheld'}
                >
                  {row.label === null ? 'withheld' : row.label === 1 ? 'Yes' : 'No'}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
