/**
 * Before any test: prove each server is the one its project expects, then pay
 * the explainer's first-call cost once, so no test's timing depends on it.
 *
 * - ``chromium``: the synthetic "dataset" server, whose customers are all ``E2E-``.
 * - ``public-demo``: the server started as the deployed demo is. It says its
 *   customers are synthetic, lists only generated ``DEMO-`` customers, and
 *   knows no ``E2E-`` customer at all.
 */
import { request, type FullConfig } from '@playwright/test';
import type { components } from '../src/api/schema';

type CustomerPage = components['schemas']['CustomerPage'];
type ModelInfo = components['schemas']['ModelInfoResponse'];

/** The prefix scripts/e2e_server.py gives every customer of its "dataset". */
export const DATASET_PREFIX = 'E2E-';
/** The prefix of a generated demo customer (src/churnsense/demo/customers.py). */
export const DEMO_PREFIX = 'DEMO-';
export const PUBLIC_PROJECT = 'public-demo';

async function prove(baseURL: string, publicDemo: boolean): Promise<void> {
  const api = await request.newContext({ baseURL });
  try {
    const page = await api.get('/api/customers?page_size=50');
    if (!page.ok()) throw new Error(`${baseURL} did not list customers: ${String(page.status())}`);
    const ids = ((await page.json()) as CustomerPage).rows.map((row) => row.id);
    const expected = publicDemo ? DEMO_PREFIX : DATASET_PREFIX;
    if (ids.length === 0 || !ids.every((id) => id.startsWith(expected))) {
      throw new Error(`${baseURL}: its customers are not all ${expected}… IDs`);
    }
    const info = (await (await api.get('/api/model-info')).json()) as ModelInfo;
    const records = info.data_source?.customer_records;
    if (records !== (publicDemo ? 'synthetic' : 'dataset')) {
      throw new Error(`${baseURL}: it says its customer records are ${String(records)}`);
    }
    if (publicDemo) {
      const known = await api.get(`/api/customers?q=${DATASET_PREFIX}`);
      if (((await known.json()) as CustomerPage).total !== 0) {
        throw new Error(`${baseURL}: a public demo lists a ${DATASET_PREFIX} customer`);
      }
    }
    // A write needs the page's own origin (the API's TrustedHosts check).
    const warm = await api.post('/api/explain', {
      data: { customer_ids: ids.slice(0, 8) },
      headers: { Origin: baseURL },
    });
    if (!warm.ok()) throw new Error(`the explainer did not warm up: ${String(warm.status())}`);
  } finally {
    await api.dispose();
  }
}

export default async function globalSetup(config: FullConfig): Promise<void> {
  for (const project of config.projects) {
    const baseURL = project.use.baseURL;
    if (!baseURL) throw new Error(`playwright.config.ts sets no baseURL for ${project.name}`);
    await prove(baseURL, project.name === PUBLIC_PROJECT);
  }
}
