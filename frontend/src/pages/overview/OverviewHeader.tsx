/**
 * The overview's header: the customer search (it filters the retention
 * queue), the provenance of the data and model, and the scored export.
 */
import { useEffect, useEffectEvent, useState } from 'react';
import { exportUrl } from '../../api/client';
import type { ModelInfo } from '../../api/queries';
import { icons } from '../../components/icons';
import { Header } from '../../components/shell/Header';
import { Button, ButtonLink } from '../../components/ui/Button';
import { SearchField } from '../../components/ui/Field';
import { Popover } from '../../components/ui/Popover';
import { useDebouncedValue } from '../../lib/useDebouncedValue';
import styles from './Overview.module.css';

/** What a customer ID can hold, as the API's search accepts it. */
const NOT_IN_AN_ID = /[^A-Za-z0-9-]/g;
const MAX_ID = 32;

interface OverviewHeaderProps {
  info: ModelInfo | undefined;
  /** The search in the address. */
  query: string;
  onSearch: (query: string) => void;
}

export function OverviewHeader({ info, query, onSearch }: OverviewHeaderProps) {
  return (
    <Header
      section="Book"
      page="Executive overview"
      brandOnSmall
      search={<Search query={query} onSearch={onSearch} />}
    >
      {info && <Provenance info={info} />}
      <ButtonLink href={exportUrl({})} download size="lg" className={styles.hideSm}>
        <icons.download size={15} />
        Export scored CSV
      </ButtonLink>
    </Header>
  );
}

/**
 * Typed text settles for 250 ms before it reaches the address (and so the
 * queue's request). A search cleared elsewhere (the queue's chip) clears the
 * box too.
 */
function Search({ query, onSearch }: { query: string; onSearch: (query: string) => void }) {
  const [draft, setDraft] = useState(query);
  const [shown, setShown] = useState(query);
  if (query !== shown) {
    setShown(query);
    setDraft(query);
  }
  const settled = useDebouncedValue(draft, 250);
  // A search runs when the draft settles, never because the address changed:
  // a search cleared elsewhere must not be put back by a stale draft.
  const search = useEffectEvent((value: string) => {
    if (value !== query) onSearch(value);
  });
  useEffect(() => {
    search(settled);
  }, [settled]);
  return (
    <SearchField
      label="Find a customer by ID"
      placeholder="Find a customer ID…"
      value={draft}
      maxLength={MAX_ID}
      onChange={(value) => setDraft(value.replace(NOT_IN_AN_ID, '').slice(0, MAX_ID))}
    />
  );
}

/** "16320c9c…5e91": enough of a digest to compare by eye. */
function short(digest: string): string {
  return `${digest.slice(0, 8)}…${digest.slice(-4)}`;
}

function Provenance({ info }: { info: ModelInfo }) {
  const provenance = info.provenance;
  const verified = provenance?.verified === true;
  return (
    <Popover
      label="Data provenance"
      className={styles.provenance}
      trigger={(props) => (
        <Button {...props} variant={verified ? 'status' : 'outline'} size="lg">
          {verified ? <icons.shield size={14} /> : <icons.warning size={14} />}
          <span>
            {/* The space stays outside the hidden word, so the name reads right at any width. */}
            <span className={styles.hideSm}>Provenance</span>{' '}
            {verified ? 'verified' : 'not verified'}
          </span>
          <icons.chevronDown size={12} />
        </Button>
      )}
    >
      <dl className={styles.facts}>
        <div>
          <dt>Dataset SHA-256</dt>
          <dd className={verified ? styles.ok : undefined}>
            {provenance?.data_sha256 ? short(provenance.data_sha256) : 'unknown'}
            {verified && ' ✓'}
          </dd>
        </div>
        <div>
          <dt>Split digest</dt>
          <dd className={verified ? styles.ok : undefined}>
            {verified ? 'matches model ✓' : 'not verified'}
          </dd>
        </div>
        <div>
          <dt>Model artifact</dt>
          <dd>{info.model_sha256 ? short(info.model_sha256) : 'unknown'}</dd>
        </div>
        <div>
          <dt>Served on</dt>
          <dd>{window.location.host} · local only</dd>
        </div>
      </dl>
      <p className={styles.factsNote}>
        {verified || !provenance?.withheld_reason
          ? "Partition membership is trusted only while both digests match the model's record; otherwise every outcome on this page is withheld."
          : `Every outcome on this page is withheld: ${provenance.withheld_reason}`}
      </p>
    </Popover>
  );
}
