/**
 * The Vite dev server serves nothing of the repository outside frontend/ and
 * the synthetic test fixtures (vite.config.ts, server.fs.allow), including
 * through the query-string tricks of past Vite advisories. Only harmless files
 * are probed; the dataset and the model are never asked for.
 */
import path from 'node:path';
import { createServer, type ViteDevServer } from 'vite';
import { expect, test } from './fixtures';

const FRONTEND = path.resolve(import.meta.dirname, '..');
const REPOSITORY = path.resolve(FRONTEND, '..');
const PORT = 5179;
const origin = `http://127.0.0.1:${String(PORT)}`;

/** Files outside the allow list, each with a line only that file holds. */
const OUTSIDE = [
  { file: 'pyproject.toml', marker: '[project]' },
  { file: 'configs/config.yaml', marker: 'random_seed' },
];
/** The plain path, and the suffixes earlier Vite releases let past the check. */
const SUFFIXES = [
  '',
  '?raw',
  '?raw??',
  '?import&raw??',
  '?inline&import',
  '?.svg?.wasm?init',
  '?url',
];

let server: ViteDevServer;

test.beforeAll(async () => {
  server = await createServer({
    configFile: path.join(FRONTEND, 'vite.config.ts'),
    root: FRONTEND,
    logLevel: 'silent',
    server: { host: '127.0.0.1', port: PORT, strictPort: true },
  });
  await server.listen();
});

test.afterAll(async () => {
  await server.close();
});

test('the dev server refuses files outside its allow list, however they are asked for', async ({
  request,
}) => {
  for (const { file, marker } of OUTSIDE) {
    for (const suffix of SUFFIXES) {
      const url = `${origin}/@fs/${path.join(REPOSITORY, file)}${suffix}`;
      const response = await request.get(url, { failOnStatusCode: false });
      const body = await response.text();
      expect(body, `${file}${suffix}`).not.toContain(marker);
      expect(response.status(), `${file}${suffix}`).not.toBe(200);
    }
  }
});

test('the dev server still serves the synthetic fixture the batch page imports', async ({
  request,
}) => {
  const url = `${origin}/@fs/${path.join(REPOSITORY, 'tests/fixtures/demo_customers.csv')}`;
  const response = await request.get(url);
  expect(response.status()).toBe(200);
  expect(await response.text()).toContain('DEMO-00000');
});
