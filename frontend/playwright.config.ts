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
 *
 * A third, ``v2-preview``, runs ``v2-preview.spec.ts`` against the Vite dev
 * server built with the V2 Preview on (VITE_CHURNSENSE_V2_PREVIEW=1), which
 * forwards /api to a third API server with its V2 routes on
 * (CHURNSENSE_V2_DATA_HUB_PREVIEW=1). The preview is local only: the served
 * build (``dist``) has no V2 code, which ``chromium`` checks.
 *
 * A fourth, ``v2-built``, runs that spec and ``v2-built.spec.ts`` against the V2
 * Preview's own build (``build/v2-preview``), served as ``make v2-preview-serve``
 * serves it, by the same script, in front of the same third API server.
 */
import { defineConfig, devices } from '@playwright/test';

const port = Number(process.env['E2E_PORT'] ?? 8799);
// 127.0.0.1, never localhost: Node may resolve that to ::1, where nothing listens.
const baseURL = `http://127.0.0.1:${String(port)}`;
const publicPort = Number(process.env['E2E_PUBLIC_PORT'] ?? port - 1);
const publicURL = `http://127.0.0.1:${String(publicPort)}`;
/** The spec that runs against the public server, and only against it. */
const PUBLIC_SPEC = /public-demo\.spec\.ts$/;
const v2ApiPort = Number(process.env['E2E_V2_API_PORT'] ?? port - 2);
const v2Port = Number(process.env['E2E_V2_PORT'] ?? 5178);
const v2URL = `http://127.0.0.1:${String(v2Port)}`;
/** The spec that runs against the V2 Preview, on its dev server and as built. */
const V2_SPEC = /v2-preview\.spec\.ts$/;
const v2BuiltPort = Number(process.env['E2E_V2_BUILT_PORT'] ?? 5177);
const v2BuiltURL = `http://127.0.0.1:${String(v2BuiltPort)}`;
/** The spec that runs against the V2 Preview as built, and only against it. */
const V2_BUILT_SPEC = /v2-built\.spec\.ts$/;
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
    {
      name: 'chromium',
      testIgnore: [PUBLIC_SPEC, V2_SPEC, V2_BUILT_SPEC],
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'public-demo',
      testMatch: PUBLIC_SPEC,
      use: { ...devices['Desktop Chrome'], baseURL: publicURL },
    },
    {
      name: 'v2-preview',
      testMatch: V2_SPEC,
      use: { ...devices['Desktop Chrome'], baseURL: v2URL },
    },
    {
      name: 'v2-built',
      testMatch: [V2_SPEC, V2_BUILT_SPEC],
      use: { ...devices['Desktop Chrome'], baseURL: v2BuiltURL },
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
    {
      ...server,
      command: `../.venv/bin/python ../scripts/e2e_server.py --port ${String(v2ApiPort)}`,
      // Ready as the others are; that its V2 routes are on, global-setup proves.
      url: `http://127.0.0.1:${String(v2ApiPort)}/api/overview`,
      env: { CHURNSENSE_V2_DATA_HUB_PREVIEW: '1' },
    },
    {
      ...server,
      command: `npx vite --port ${String(v2Port)} --strictPort`,
      url: v2URL,
      env: { VITE_CHURNSENSE_V2_PREVIEW: '1', API_PORT: String(v2ApiPort) },
    },
    {
      ...server,
      // Started after the API above is ready (Playwright starts them in order): the
      // script refuses to serve without it, and without the V2 Preview build.
      command: `../.venv/bin/python ../scripts/v2_preview.py serve --api-port ${String(v2ApiPort)} --web-port ${String(v2BuiltPort)}`,
      url: v2BuiltURL,
    },
  ],
});
