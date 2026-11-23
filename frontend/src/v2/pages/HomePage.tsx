/**
 * The preview's front page: what it holds, as two separate tools. It asks the
 * API for nothing.
 */
import { Header } from '../../components/shell/Header';
import { ButtonLink } from '../../components/ui/Button';
import { InfoNote } from '../../components/ui/InfoNote';
import { Page } from '../../components/ui/Page';
import { PageHeader } from '../../components/ui/PageHeader';
import { Card, CardHeader } from '../../components/ui/Surface';
import { BOUNDARY, PREVIEW_NOTE, SECTION } from '../copy';
import styles from '../V2.module.css';

export default function HomePage() {
  return (
    <>
      <Header section={SECTION} page="Preview home" />
      <Page>
        <PageHeader eyebrow="V2 Preview · local only" title="Retention workspace preview">
          An early look at ChurnSense for SaaS teams: check an account file against the account
          contract, and explore how a portfolio’s retention signals are presented.
        </PageHeader>
        <InfoNote title={BOUNDARY.title}>{BOUNDARY.body}</InfoNote>
        <div className={styles.grid2}>
          <Card titleId="home-data-hub">
            <CardHeader
              id="home-data-hub"
              title="Data Hub: check your account file"
              subtitle="Your file · checked, never saved"
            />
            <p className={styles.body}>
              Upload a CSV of account snapshots. The server maps its columns to the account
              contract, checks every row and reports what it would import: accepted and rejected
              rows, warnings, and MRR per snapshot date and currency.
            </p>
            <p className={styles.muted}>
              The file is checked in memory and discarded. Nothing is imported or stored, and it
              does not appear in the portfolio demo.
            </p>
            <div className={styles.row}>
              <ButtonLink to="/v2/data-hub" variant="primary">
                Open the Data Hub
              </ButtonLink>
            </div>
          </Card>
          <Card titleId="home-portfolio">
            <CardHeader
              id="home-portfolio"
              title="Portfolio demo: synthetic SaaS accounts"
              subtitle="Generated data · fixed snapshot date"
            />
            <p className={styles.body}>
              A generated portfolio of SaaS accounts, assessed by transparent rules: which accounts
              need attention or a watch, why, when they renew and how much recurring revenue sits
              with them, by currency.
            </p>
            <p className={styles.muted}>
              The rules are operating heuristics, not a model and not a probability. The data is
              synthetic: it is not your file and not real customers.
            </p>
            <div className={styles.row}>
              <ButtonLink to="/v2/portfolio" variant="primary">
                Open the portfolio
              </ButtonLink>
              <ButtonLink to="/v2/accounts" variant="secondary">
                Browse accounts
              </ButtonLink>
            </div>
          </Card>
        </div>
        <p className={styles.muted}>
          <strong>{PREVIEW_NOTE.title}</strong> {PREVIEW_NOTE.body}
        </p>
      </Page>
    </>
  );
}
