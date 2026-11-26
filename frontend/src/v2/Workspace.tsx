/**
 * The V2 Preview workspace: its own navigation in the dashboard's frame, and
 * the page the address names. Loaded only from a build made with the preview
 * on (``flag``); a page that fails to render is contained, as in the dashboard.
 */
import { Suspense } from 'react';
import { PageBoundary } from '../app/App';
import { useLocation } from '../app/router';
import { Header } from '../components/shell/Header';
import { Shell } from '../components/shell/Shell';
import { ButtonLink } from '../components/ui/Button';
import { Page } from '../components/ui/Page';
import { PageHeader } from '../components/ui/PageHeader';
import { Skeleton } from '../components/ui/States';
import { SECTION } from './copy';
import { V2_ROOT } from './flag';
import { v2RouteFor } from './routes';
import { V2Sidebar } from './V2Sidebar';

export default function Workspace() {
  const { pathname } = useLocation();
  const route = v2RouteFor(pathname);
  return (
    <Shell sidebar={V2Sidebar}>
      {route ? (
        <PageBoundary key={route.path} section={SECTION} page={route.label}>
          <Suspense
            fallback={
              <Page>
                <Skeleton label="Loading the page" rows={4} rowHeight={120} />
              </Page>
            }
          >
            <route.page />
          </Suspense>
        </PageBoundary>
      ) : (
        <>
          <Header section={SECTION} page="Not found" />
          <Page>
            <PageHeader eyebrow="Not found" title="There is no preview page here">
              The address does not match any page of the V2 Preview.
            </PageHeader>
            <ButtonLink to={V2_ROOT} variant="secondary">
              Preview home
            </ButtonLink>
          </Page>
        </>
      )}
    </Shell>
  );
}
