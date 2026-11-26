/**
 * Portfolio (M2): ``GET /api/v2/portfolio/summary`` of the synthetic dataset.
 * Counts by rule level and status, MRR per currency, renewals in the rule
 * windows and the rules themselves - each as the API computed it.
 */
import { Header } from '../../components/shell/Header';
import { ButtonLink } from '../../components/ui/Button';
import { InfoNote } from '../../components/ui/InfoNote';
import { Page } from '../../components/ui/Page';
import { PageHeader } from '../../components/ui/PageHeader';
import { ErrorState, Skeleton } from '../../components/ui/States';
import { Card, CardHeader, StatTile } from '../../components/ui/Surface';
import { usePortfolioSummary, type CurrencySummary, type PortfolioSummary } from '../api';
import { EXPOSED_MRR, LEVEL_LABEL, SECTION } from '../copy';
import { amount, count, plural } from '../format';
import { DatasetBanner, Notes, SyntheticNote } from '../parts';
import styles from '../V2.module.css';

const TITLE_ID = 'portfolio-title';

export default function PortfolioPage() {
  const summary = usePortfolioSummary();
  return (
    <>
      <Header section={SECTION} page="Portfolio" />
      <Page>
        <PageHeader eyebrow="Synthetic demo · rules, not a model" title="Portfolio">
          Where attention sits in a generated SaaS portfolio: accounts by rule level, upcoming
          renewals, and the recurring revenue of each level in each currency.
        </PageHeader>
        <h2 id={TITLE_ID} tabIndex={-1} className="sr">
          Portfolio summary
        </h2>
        {summary.isPending ? (
          <Skeleton label="Loading the portfolio" rows={4} rowHeight={96} />
        ) : summary.isError ? (
          <ErrorState onRetry={() => void summary.refetch()} focusTo={TITLE_ID}>
            The portfolio summary could not be loaded. The local server may be unreachable, or its
            V2 preview switch may be off.
          </ErrorState>
        ) : (
          <Summary data={summary.data} />
        )}
      </Page>
    </>
  );
}

function Summary({ data }: { data: PortfolioSummary }) {
  return (
    <>
      <DatasetBanner dataset={data.dataset} />
      <SyntheticNote />
      <div className={styles.tiles} role="group" aria-label="Accounts by rule level">
        <StatTile label="Accounts" value={count(data.total_accounts)} sub="in the dataset" />
        <StatTile
          accent
          label={LEVEL_LABEL.attention}
          value={count(data.levels.attention)}
          sub="assessed accounts"
          link={{ to: '/v2/accounts?level=attention', description: 'List these accounts' }}
        />
        <StatTile
          label={LEVEL_LABEL.watch}
          value={count(data.levels.watch)}
          sub="assessed accounts"
          link={{ to: '/v2/accounts?level=watch', description: 'List these accounts' }}
        />
        <StatTile
          label={LEVEL_LABEL.no_signals}
          value={count(data.levels.no_signals)}
          sub="assessed accounts"
          link={{ to: '/v2/accounts?level=no_signals', description: 'List these accounts' }}
        />
        <StatTile
          label={LEVEL_LABEL.not_assessed}
          value={count(data.excluded_accounts)}
          sub={`${count(data.not_assessed.trialing)} trialing · ${count(data.not_assessed.paused)} paused · ${count(data.not_assessed.canceled)} canceled`}
          link={{ to: '/v2/accounts?level=not_assessed', description: 'List these accounts' }}
        />
      </div>
      <p className={styles.muted}>
        {plural(data.eligible_accounts, 'account')} are assessed (active or past due). Trialing,
        paused and canceled accounts are not assessed, never shown as accounts without risk.
      </p>

      <Card titleId="currencies-title" variant="flush">
        <div className={styles.stack}>
          <div className={styles.inset}>
            <CardHeader
              id="currencies-title"
              title="Monthly recurring revenue by currency"
              subtitle="Each currency on its own row. Amounts in two currencies are never added together."
            />
          </div>
          <Currencies currencies={data.currencies} />
          <div className={styles.inset}>
            <InfoNote title="What exposed MRR means.">{EXPOSED_MRR}</InfoNote>
          </div>
        </div>
      </Card>

      <div className={styles.grid2}>
        <Card titleId="renewals-title">
          <CardHeader
            id="renewals-title"
            title="Upcoming renewals"
            subtitle="Assessed accounts renewing within each rule window: counts, not amounts."
          />
          {data.renewals.length === 0 ? (
            <p className={styles.muted}>No assessed account renews within a rule window.</p>
          ) : (
            <dl className={styles.facts}>
              {data.renewals.map((window) => (
                <div key={`${String(window.from_days)}-${String(window.to_days)}`}>
                  <dt>
                    {count(window.from_days)}–{count(window.to_days)} days · {window.severity}
                  </dt>
                  <dd>{plural(window.accounts, 'account')}</dd>
                </div>
              ))}
            </dl>
          )}
          <div className={styles.row}>
            <ButtonLink to="/v2/accounts?sort=renewal_asc" variant="secondary" size="sm">
              Accounts by nearest renewal
            </ButtonLink>
          </div>
        </Card>
        <Card titleId="rules-title">
          <CardHeader
            id="rules-title"
            title="How accounts are assessed"
            subtitle={`${data.methodology.method_version} · ${data.methodology.method}`}
          />
          <p className={styles.body}>{data.methodology.statement}</p>
          <ul aria-label="Rules" className={styles.list}>
            {data.methodology.rules.map((rule) => (
              <li key={rule.code}>
                <span className={styles.mono}>{rule.code}</span>: {rule.rule}
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card titleId="definitions-title">
        <CardHeader id="definitions-title" title="Definitions and notes" />
        <dl className={styles.facts}>
          {Object.entries(data.definitions).map(([term, meaning]) => (
            <div key={term}>
              <dt>{term.replaceAll('_', ' ')}</dt>
              <dd className={styles.body}>{meaning}</dd>
            </div>
          ))}
        </dl>
        <Notes notes={data.notes} />
      </Card>
    </>
  );
}

function Currencies({ currencies }: { currencies: readonly CurrencySummary[] }) {
  if (currencies.length === 0) {
    return <p className={`${styles.muted} ${styles.inset}`}>No account holds recurring revenue.</p>;
  }
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th scope="col">Currency</th>
            <th scope="col" className={styles.num}>
              Accounts
            </th>
            <th scope="col" className={styles.num}>
              Active MRR
            </th>
            <th scope="col" className={styles.num}>
              Attention MRR
            </th>
            <th scope="col" className={styles.num}>
              Watch MRR
            </th>
            <th scope="col" className={styles.num}>
              No-signals MRR
            </th>
            <th scope="col" className={styles.num}>
              Exposed MRR
            </th>
          </tr>
        </thead>
        <tbody>
          {currencies.map((c) => (
            <tr key={c.currency}>
              <th scope="row" className={`${styles.mono} ${styles.strong}`}>
                {c.currency}
              </th>
              <td className={styles.num}>
                {count(c.accounts)}
                <span className={styles.cellNote}>
                  {count(c.eligible_accounts)} assessed · {count(c.excluded_accounts)} not
                </span>
              </td>
              <td className={styles.num}>{amount(c.active_mrr, c.currency)}</td>
              <td className={styles.num}>
                {amount(c.attention_mrr, c.currency)}
                <span className={styles.cellNote}>{plural(c.levels.attention, 'account')}</span>
              </td>
              <td className={styles.num}>
                {amount(c.watch_mrr, c.currency)}
                <span className={styles.cellNote}>{plural(c.levels.watch, 'account')}</span>
              </td>
              <td className={styles.num}>
                {amount(c.no_signals_mrr, c.currency)}
                <span className={styles.cellNote}>{plural(c.levels.no_signals, 'account')}</span>
              </td>
              <td className={`${styles.num} ${styles.strong}`}>
                {amount(c.exposed_mrr, c.currency)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
