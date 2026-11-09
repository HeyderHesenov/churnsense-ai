/**
 * Before any test: prove the server is the synthetic one, then pay the
 * explainer's first-call cost once, so no test's timing depends on it.
 */
import { request, type FullConfig } from '@playwright/test';
import type { components } from '../src/api/schema';

type CustomerPage = components['schemas']['CustomerPage'];

/** The prefix scripts/e2e_server.py gives every synthetic customer. */
const SYNTHETIC = 'E2E-';

export default async function globalSetup(config: FullConfig): Promise<void> {
  const baseURL = config.projects[0]?.use.baseURL;
  if (!baseURL) throw new Error('playwright.config.ts sets no baseURL');
  const api = await request.newContext({ baseURL });
  try {
    const page = await api.get('/api/customers?page_size=50');
    if (!page.ok()) throw new Error(`the e2e server did not list customers: ${page.status()}`);
    const ids = ((await page.json()) as CustomerPage).rows.map((row) => row.id);
    if (ids.length === 0 || !ids.every((id) => id.startsWith(SYNTHETIC))) {
      throw new Error(`not the e2e server: its customers are not all ${SYNTHETIC}… IDs`);
    }
    // A write needs the page's own origin (the API's TrustedHosts check).
    const warm = await api.post('/api/explain', {
      data: { customer_ids: ids.slice(0, 8) },
      headers: { Origin: baseURL },
    });
    if (!warm.ok()) throw new Error(`the explainer did not warm up: ${warm.status()}`);
  } finally {
    await api.dispose();
  }
}
