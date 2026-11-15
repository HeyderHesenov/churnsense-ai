/**
 * The filter bar. Choices (a chip, a select, the flag) go to the address at
 * once and add a history entry; typed text (the ID, a tenure bound) settles
 * for 250 ms and then edits the entry in place. Every change starts again at
 * the first page.
 */
import { useId } from 'react';
import type { SetParams } from '../../app/params';
import { Button } from '../../components/ui/Button';
import { Select } from '../../components/ui/Field';
import { Segmented, ToggleChip, type Option } from '../../components/ui/Toggle';
import { BAND_NAMES } from '../../lib/bands';
import { cleanCustomerId, MAX_CUSTOMER_ID } from '../../lib/customerId';
import { useSynthetic } from '../../components/ui/DataSourceNote';
import { PARTITION_OPTIONS } from '../../lib/labels';
import { cx } from '../../lib/style';
import { useSettledDraft } from '../../app/useSettledDraft';
import { flagCaption } from './copy';
import {
  activeFilters,
  CONTRACT_LABELS,
  INTERNET_LABELS,
  PAYMENT_LABELS,
  RESET,
  tenureInvalid,
  tenureText,
  tenureValue,
  type EXPLORER_PARAMS,
  type ExplorerState,
} from './explorer';
import styles from './Explorer.module.css';

/** "Any", then each of the API's values under the artboard's words. */
function options<T extends string>(labels: Record<T, string>): Option<T | 'any'>[] {
  const chosen = (Object.keys(labels) as T[]).map((value) => ({ value, label: labels[value] }));
  return [{ value: 'any', label: 'Any' }, ...chosen];
}

const CONTRACTS = options(CONTRACT_LABELS);
const INTERNET = options(INTERNET_LABELS);
const PAYMENTS = options(PAYMENT_LABELS);
const FLAG = [
  { value: 'all', label: 'All' },
  { value: 'true', label: 'Flagged' },
  { value: 'false', label: 'Not flagged' },
] as const;

interface FiltersProps {
  state: ExplorerState;
  set: SetParams<typeof EXPLORER_PARAMS>;
  /** The served decision threshold, once ``/api/model-info`` has answered. */
  threshold: number | undefined;
}

export function Filters({ state, set, threshold }: FiltersProps) {
  const active = activeFilters(state);
  const caption = flagCaption(threshold);
  const tenureId = useId();
  const invalidId = useId();
  const invalid = tenureInvalid(state);
  const synthetic = useSynthetic();
  const toggleBand = (band: (typeof BAND_NAMES)[number]) =>
    set({
      bands: state.bands.includes(band)
        ? state.bands.filter((b) => b !== band)
        : [...state.bands, band],
      page: 1,
    });

  return (
    <section aria-label="Filters" className={styles.filters}>
      <IdSearch query={state.q} onSearch={(q) => set({ q, page: 1 }, { replace: true })} />

      <div className={styles.field}>
        <span className={styles.caption}>Band</span>
        <div role="group" aria-label="Band" className={styles.chips}>
          {BAND_NAMES.map((band) => (
            <ToggleChip
              key={band}
              variant="band"
              band={band}
              pressed={state.bands.includes(band)}
              onToggle={() => toggleBand(band)}
            >
              {band}
            </ToggleChip>
          ))}
        </div>
      </div>

      <Select
        label="Contract"
        layout="stacked"
        value={state.contract ?? 'any'}
        options={CONTRACTS}
        onChange={(v) => set({ contract: v === 'any' ? null : v, page: 1 })}
      />
      <Select
        label="Internet"
        layout="stacked"
        value={state.internet ?? 'any'}
        options={INTERNET}
        onChange={(v) => set({ internet: v === 'any' ? null : v, page: 1 })}
      />
      <Select
        label="Payment"
        layout="stacked"
        value={state.payment ?? 'any'}
        options={PAYMENTS}
        onChange={(v) => set({ payment: v === 'any' ? null : v, page: 1 })}
      />

      <div className={styles.field}>
        <span id={tenureId} className={styles.caption}>
          Tenure, months
        </span>
        <div role="group" aria-labelledby={tenureId} className={styles.range}>
          <TenureBox
            label="Minimum tenure"
            value={state.tenure_min}
            invalid={invalid ? invalidId : null}
            onSettle={(v) => set({ tenure_min: v, page: 1 }, { replace: true })}
          />
          <span aria-hidden="true">–</span>
          <TenureBox
            label="Maximum tenure"
            value={state.tenure_max}
            invalid={invalid ? invalidId : null}
            onSettle={(v) => set({ tenure_max: v, page: 1 }, { replace: true })}
          />
        </div>
        {invalid && (
          <span id={invalidId} className={styles.invalid}>
            Minimum is above maximum.
          </span>
        )}
      </div>

      <div className={styles.field}>
        <span className={styles.caption} aria-hidden="true">
          {caption}
        </span>
        <Segmented
          label={caption}
          compact
          small
          options={FLAG}
          value={state.flagged ?? 'all'}
          onChange={(v) => set({ flagged: v === 'all' ? null : v, page: 1 })}
        />
      </div>

      {/* Generated customers are in no partition of the dataset: there is nothing to choose. */}
      {!synthetic && (
        <Select
          label="Partition"
          layout="stacked"
          value={state.part ?? 'all'}
          options={PARTITION_OPTIONS}
          onChange={(v) => set({ part: v === 'all' ? null : v, page: 1 })}
        />
      )}

      {/* Marked, not disabled, with nothing to reset: pressing it must not drop the focus. */}
      <Button
        variant="ghost"
        size="md"
        className={styles.reset}
        aria-disabled={active === 0}
        onClick={() => {
          if (active > 0) set(RESET);
        }}
      >
        {active === 0 ? 'Reset' : `Reset (${String(active)})`}
      </Button>
    </section>
  );
}

function IdSearch({ query, onSearch }: { query: string; onSearch: (query: string) => void }) {
  const id = useId();
  const [draft, setDraft] = useSettledDraft(query, onSearch);
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.caption}>
        Customer ID
      </label>
      <input
        id={id}
        type="search"
        className={cx(styles.input, styles.id)}
        placeholder="e.g. 1234-ABCDE"
        value={draft}
        maxLength={MAX_CUSTOMER_ID}
        autoComplete="off"
        spellCheck={false}
        onChange={(event) => setDraft(cleanCustomerId(event.target.value))}
      />
    </div>
  );
}

interface TenureBoxProps {
  label: string;
  value: number | null;
  /** The id of the message saying why the range is invalid, while it is. */
  invalid: string | null;
  onSettle: (value: number | null) => void;
}

/** Digits only, as the bound they mean (``type="number"`` would read "1e" as empty and clear it). */
function TenureBox({ label, value, invalid, onSettle }: TenureBoxProps) {
  const id = useId();
  const [draft, setDraft] = useSettledDraft(value === null ? '' : String(value), (text) =>
    onSettle(tenureValue(text)),
  );
  return (
    <>
      <label htmlFor={id} className="sr">
        {label}
      </label>
      <input
        id={id}
        type="text"
        inputMode="numeric"
        className={cx(styles.input, styles.tenure)}
        value={draft}
        autoComplete="off"
        aria-invalid={invalid !== null}
        aria-describedby={invalid ?? undefined}
        onChange={(event) => setDraft(tenureText(event.target.value))}
      />
    </>
  );
}
