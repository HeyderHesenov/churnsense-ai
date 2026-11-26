/**
 * Account (M2): ``GET /api/v2/accounts/{id}``, one synthetic account - its
 * subscription, the latest snapshot, the rules' assessment with the evidence
 * behind each signal, and every snapshot's own assessment. All of it is the
 * API's; the page only lays it out.
 */
import type { ReactNode } from 'react';
import { ApiError } from '../../api/client';
import { param, useParams } from '../../app/params';
import { Header } from '../../components/shell/Header';
import { ButtonLink } from '../../components/ui/Button';
import { InfoNote } from '../../components/ui/InfoNote';
import { Page } from '../../components/ui/Page';
import { PageHeader } from '../../components/ui/PageHeader';
import { EmptyState, ErrorState, Skeleton } from '../../components/ui/States';
import { Card, CardHeader } from '../../components/ui/Surface';
import { useAccount, type AccountDetail, type Assessment, type Indicator } from '../api';
import { SECTION, STATUS_LABEL } from '../copy';
import { amount, count, longDate, plural, shortDate } from '../format';
import { DatasetBanner, LevelBadge, Notes, SyntheticNote } from '../parts';
import styles from '../V2.module.css';

/** An account ID as the contract allows one: printable, at most 128 characters. */
const ACCOUNT_PARAMS = {
  id: param.text({ pattern: /^[^\p{Cc}]*$/u, maxLength: 128 }),
};

const TITLE_ID = 'account-title';

export default function AccountPage() {
  const [{ id }] = useParams(ACCOUNT_PARAMS);
  const account = useAccount(id || null);
  // One frame in every state, so its heading (a retry's focus target) is never remounted.
  return (
    <Frame page={account.data ? (account.data.account_name ?? account.data.account_id) : 'Account'}>
      <Body id={id} account={account} />
    </Frame>
  );
}

function Body({ id, account }: { id: string; account: ReturnType<typeof useAccount> }) {
  if (!id) {
    return (
      <>
        <EmptyState title="No account chosen">
          Open an account from the list to see its signals, evidence and history.
        </EmptyState>
        <BackToList />
      </>
    );
  }
  if (account.isPending) {
    return <Skeleton label="Loading the account" rows={4} rowHeight={110} />;
  }
  if (account.isError) {
    const missing = account.error instanceof ApiError && account.error.status === 404;
    return (
      <>
        {missing ? (
          <EmptyState title="There is no such account">
            The synthetic dataset has no account with this ID.
          </EmptyState>
        ) : (
          <ErrorState onRetry={() => void account.refetch()} focusTo={TITLE_ID}>
            The account could not be loaded. The local server may be unreachable, or its V2 preview
            switch may be off.
          </ErrorState>
        )}
        <BackToList />
      </>
    );
  }
  return <Detail data={account.data} />;
}

function Frame({ page, children }: { page: string; children: ReactNode }) {
  return (
    <>
      <Header section={SECTION} page={page} />
      <Page>
        {/* Where a retry leaves focus: here in every state, so it survives the reload. */}
        <h2 id={TITLE_ID} tabIndex={-1} className="sr">
          Account
        </h2>
        {children}
      </Page>
    </>
  );
}

function BackToList() {
  return (
    <div className={styles.row}>
      <ButtonLink to="/v2/accounts" variant="secondary">
        Back to the accounts
      </ButtonLink>
    </div>
  );
}

function Detail({ data }: { data: AccountDetail }) {
  const { subscription: sub, latest, assessment } = data;
  return (
    <>
      <PageHeader
        eyebrow={`Synthetic account · ${data.account_id}`}
        title={data.account_name ?? data.account_id}
      >
        <span className={styles.row}>
          <LevelBadge level={assessment.level} />
          <span>{STATUS_LABEL[sub.status]}</span>
          {latest.segment && <span>· {latest.segment}</span>}
        </span>
      </PageHeader>
      <DatasetBanner dataset={data.dataset} />
      <SyntheticNote />

      <InfoNote title="Where to look first.">{data.next_step_context}</InfoNote>

      <div className={styles.grid2}>
        <Card titleId="subscription-title">
          <CardHeader id="subscription-title" title="Subscription" />
          <dl className={styles.facts}>
            <Fact term="Status">{STATUS_LABEL[sub.status]}</Fact>
            <Fact term="MRR">{amount(sub.mrr, sub.currency)}</Fact>
            <Fact term="Customer since">
              {sub.customer_since ? shortDate(sub.customer_since) : '—'}
              {sub.tenure_days !== null && (
                <span className={styles.cellNote}>{plural(sub.tenure_days, 'day')}</span>
              )}
            </Fact>
            <Fact term="Renewal">
              {sub.renewal_date ? shortDate(sub.renewal_date) : '—'}
              {sub.days_to_renewal !== null && (
                <span className={styles.cellNote}>in {plural(sub.days_to_renewal, 'day')}</span>
              )}
            </Fact>
            {sub.canceled_at && <Fact term="Canceled">{shortDate(sub.canceled_at)}</Fact>}
            {sub.cancellation_reason && (
              <Fact term="Cancellation reason">{sub.cancellation_reason}</Fact>
            )}
          </dl>
        </Card>
        <Card titleId="latest-title">
          <CardHeader
            id="latest-title"
            title="Latest snapshot"
            subtitle={longDate(latest.as_of_date)}
          />
          <dl className={styles.facts}>
            <Fact term="Active users">{maybe(latest.active_users)}</Fact>
            <Fact term="Seats purchased">{maybe(latest.seats_purchased)}</Fact>
            <Fact term="Sessions, 30 days">{maybe(latest.sessions_30d)}</Fact>
            <Fact term="Key actions, 30 days">{maybe(latest.key_actions_30d)}</Fact>
            <Fact term="Last activity">
              {latest.last_activity_at ? shortDate(latest.last_activity_at) : '—'}
            </Fact>
            <Fact term="Company">
              {[latest.company_size_band, latest.industry].filter(Boolean).join(' · ') || '—'}
            </Fact>
          </dl>
        </Card>
      </div>

      <AssessmentCard assessment={assessment} />
      <History data={data} />
      <Notes notes={data.notes} />
      <BackToList />
    </>
  );
}

function AssessmentCard({ assessment }: { assessment: Assessment }) {
  return (
    <Card titleId="assessment-title">
      <CardHeader
        id="assessment-title"
        title="Why this level"
        subtitle={`Rules ${assessment.method_version}, as of ${longDate(assessment.as_of_date)}${
          assessment.trend_compared_with
            ? `, trends compared with ${shortDate(assessment.trend_compared_with)}`
            : ''
        }`}
      />
      {assessment.eligibility === 'not_assessed' ? (
        <p className={styles.body}>
          <strong>Not assessed.</strong> {assessment.not_assessed_reason ?? ''}
        </p>
      ) : assessment.indicators.length === 0 ? (
        <p className={styles.body}>No rule found a signal on this snapshot.</p>
      ) : (
        <ul aria-label="Signals" className={styles.stack}>
          {assessment.indicators.map((indicator) => (
            <Signal key={indicator.code} indicator={indicator} />
          ))}
        </ul>
      )}
      {assessment.rules_not_applied.length > 0 && (
        <div className={styles.stack}>
          <p className={styles.legend}>Rules that could not be applied</p>
          <ul aria-label="Rules not applied" className={styles.list}>
            {assessment.rules_not_applied.map((r) => (
              <li key={r.rule}>
                <span className={styles.mono}>{r.rule}</span>: {r.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function Signal({ indicator }: { indicator: Indicator }) {
  return (
    <li className={styles.signal}>
      <div className={styles.row}>
        <LevelBadge level={indicator.severity} />
        <span className={styles.strong}>{indicator.title}</span>
        {indicator.observed !== null && (
          <span className={styles.mono}>{observed(indicator.observed, indicator.unit)}</span>
        )}
      </div>
      <p className={styles.body}>{indicator.explanation}</p>
      <p className={styles.muted}>
        Rule: {indicator.rule} · observed on {shortDate(indicator.observed_on)}
        {indicator.compared_with ? `, compared with ${shortDate(indicator.compared_with)}` : ''}
      </p>
      {indicator.evidence.length > 0 && (
        <dl className={styles.facts} aria-label={`Evidence: ${indicator.title}`}>
          {indicator.evidence.map((e) => (
            <div key={`${e.field}-${e.as_of_date}`}>
              <dt>
                {e.field} · {shortDate(e.as_of_date)}
              </dt>
              <dd className={styles.mono}>{String(e.value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

function History({ data }: { data: AccountDetail }) {
  return (
    <Card titleId="history-title" variant="flush">
      <div className={styles.stack}>
        <div className={styles.inset}>
          <CardHeader
            id="history-title"
            title="Snapshot history"
            subtitle={`${plural(data.history.length, 'snapshot')}, each with the level the rules gave it then.`}
          />
        </div>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Snapshot</th>
                <th scope="col">Status</th>
                <th scope="col" className={styles.num}>
                  MRR
                </th>
                <th scope="col" className={styles.num}>
                  Active users
                </th>
                <th scope="col" className={styles.num}>
                  Sessions, 30 days
                </th>
                <th scope="col" className={styles.num}>
                  Key actions, 30 days
                </th>
                <th scope="col">Level</th>
              </tr>
            </thead>
            <tbody>
              {data.history.map(({ snapshot, assessment }) => (
                <tr key={snapshot.as_of_date}>
                  <th scope="row">{shortDate(snapshot.as_of_date)}</th>
                  <td>{STATUS_LABEL[snapshot.status]}</td>
                  <td className={styles.num}>{amount(snapshot.mrr, snapshot.currency)}</td>
                  <td className={styles.num}>{maybe(snapshot.active_users)}</td>
                  <td className={styles.num}>{maybe(snapshot.sessions_30d)}</td>
                  <td className={styles.num}>{maybe(snapshot.key_actions_30d)}</td>
                  <td>
                    <LevelBadge level={assessment.level} />
                    {assessment.indicators.length > 0 && (
                      <span className={styles.cellNote}>
                        {assessment.indicators.map((i) => i.title).join(', ')}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div>
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function maybe(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : count(value);
}

/** The API's observed value with its unit; the value itself is the API's text. */
function observed(value: string, unit: Indicator['unit']): string {
  if (unit === 'percent') return `${value}%`;
  if (unit === 'days') return `${value} days`;
  return value;
}
