/**
 * The app: the shell around the page the address names. Each page is loaded
 * on first visit, and a page that fails to render is contained - the shell
 * and the other pages keep working, and the failed one can be tried again.
 */
import { Component, Suspense, type ErrorInfo, type ReactNode } from 'react';
import { Header } from '../components/shell/Header';
import { Shell } from '../components/shell/Shell';
import { ButtonLink } from '../components/ui/Button';
import { Page } from '../components/ui/Page';
import { PageHeader } from '../components/ui/PageHeader';
import { ErrorState, Skeleton } from '../components/ui/States';
import { useLocation } from './router';
import { routeFor } from './routes';

export function App() {
  const { pathname } = useLocation();
  const route = routeFor(pathname);
  return (
    <Shell>
      {route ? (
        // Keyed by page, so a failure on one page is forgotten on the next.
        <PageBoundary key={route.path} section={route.group} page={route.label}>
          <Suspense fallback={<PageLoading />}>
            <route.page />
          </Suspense>
        </PageBoundary>
      ) : (
        <NotFound />
      )}
    </Shell>
  );
}

function PageLoading() {
  return (
    <Page>
      <Skeleton label="Loading the page" rows={4} rowHeight={120} />
    </Page>
  );
}

function NotFound() {
  return (
    <>
      <Header section="ChurnSense AI" page="Not found" />
      <Page>
        <PageHeader eyebrow="Not found" title="There is no page here">
          The address does not match any page of the dashboard.
        </PageHeader>
        <ButtonLink to="/" variant="secondary" aria-label="Go to the executive overview">
          Executive overview
        </ButtonLink>
      </Page>
    </>
  );
}

interface BoundaryProps {
  section: string;
  page: string;
  children: ReactNode;
}

/**
 * Contains a page that throws while rendering; its message is never shown.
 * The way back is a full load of the same address: a page whose code failed
 * to download stays failed in this load (``lazy`` keeps the error), and after
 * a rebuild only a fresh load knows the new file names.
 */
export class PageBoundary extends Component<BoundaryProps, { failed: boolean }> {
  override state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    // For whoever runs the dashboard: the console of their own browser.
    console.error('A page failed to render', error, info.componentStack);
  }

  override render() {
    if (!this.state.failed) return this.props.children;
    return (
      <>
        <Header section={this.props.section} page={this.props.page} />
        <Page>
          <ErrorState
            action={
              <ButtonLink href={window.location.pathname + window.location.search}>
                Reload the page
              </ButtonLink>
            }
          >
            This page could not be shown.
          </ErrorState>
        </Page>
      </>
    );
  }
}
