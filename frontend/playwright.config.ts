/**
 * Browser tests of the built app, served by the real API on a synthetic book,
 * model and SHAP cache (../scripts/e2e_server.py). Nothing here reaches the real
 * dataset: the server builds everything in a temporary folder, and the global
 * setup refuses any server whose customers are not the synthetic ones.
 *
 * Two servers, two projects. ``chromium`` runs every spec but one against the
 * server that lists its synthetic "dataset". ``public-demo`` runs
 * ``public-demo.spec.ts`` against a second server started as the deployed demo
 * is (``--public``): generated customers listed, its "dataset" deleted before
 * it serves.
 */
import { defineConfig, devices } from '@playwright/test';

const port = Number(process.env['E2E_PORT'] ?? 8799);
// 127.0.0.1, never localhost: Node may resolve that to ::1, where nothing listens.
const baseURL = `http://127.0.0.1:${String(port)}`;
const publicPort = Number(process.env['E2E_PUBLIC_PORT'] ?? port - 1);
const publicURL = `http://127.0.0.1:${String(publicPort)}`;
/** The spec that runs against the public server, and only against it. */
const PUBLIC_SPEC = /public-demo\.spec\.ts$/;
const server = {
  // Never another server on the port: it could be serving the real book.
  reuseExistingServer: false,
  timeout: 240_000,
  stdout: 'pipe',
  stderr: 'pipe',
  gracefulShutdown: { signal: 'SIGTERM', timeout: 10_000 },
} as const;

export default defineConfig({
  testDir: 'e2e',
  globalSetup: './e2e/global-setup.ts',
  // The server explains three customers at a time; two workers stay within that.
  workers: 2,
  fullyParallel: false,
  retries: 0,
  forbidOnly: Boolean(process.env['CI']),
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: process.env['CI'] ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL,
    locale: 'en-US',
    timezoneId: 'UTC',
    colorScheme: 'dark',
    // No transition in flight when axe measures contrast or a layout is read.
    reducedMotion: 'reduce',
    serviceWorkers: 'block',
    // Synthetic data only, so a failure's trace and screenshot may be kept.
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    { name: 'chromium', testIgnore: PUBLIC_SPEC, use: { ...devices['Desktop Chrome'] } },
    {
      name: 'public-demo',
      testMatch: PUBLIC_SPEC,
      use: { ...devices['Desktop Chrome'], baseURL: publicURL },
    },
  ],
  webServer: [
    {
      ...server,
      command: `../.venv/bin/python ../scripts/e2e_server.py --port ${String(port)}`,
      // 200 only once the model and the book are loaded; /health answers before.
      url: `${baseURL}/api/overview`,
    },
    {
      ...server,
      command: `../.venv/bin/python ../scripts/e2e_server.py --public --port ${String(publicPort)}`,
      url: `${publicURL}/api/overview`,
    },
  ],
});
