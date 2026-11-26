/// <reference types="vitest/config" />
import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin, type ProxyOptions } from 'vite';

// The API the dev and preview servers forward to: `make api` (or `make api API_PORT=8001`).
const apiPort = process.env.API_PORT ?? '8000';

// changeOrigin stays false: the API sees the dev server's own host and
// origin, which its same-origin check accepts like the built app's.
const proxy: Record<string, ProxyOptions> = {
  '/api': { target: `http://127.0.0.1:${apiPort}`, changeOrigin: false },
};

/** The V2 Preview build's own folder: ignored by Git, and outside the Docker build context. */
const V2_PREVIEW_OUT = resolve(import.meta.dirname, '../build/v2-preview');

/**
 * Refuses a build whose folder and V2 switch disagree, before anything is
 * written: `dist` - what the API serves and every deployment ships - never
 * receives a build with the V2 Preview on, wherever the switch was set (the
 * shell or an .env file), and the preview's folder never one with it off.
 */
const v2PreviewBuildsApart: Plugin = {
  name: 'v2-preview-builds-apart',
  apply: 'build',
  configResolved({ env, root, build }) {
    const on = env['VITE_CHURNSENSE_V2_PREVIEW'] === '1';
    if (on !== (resolve(root, build.outDir) === V2_PREVIEW_OUT)) {
      throw new Error(
        on
          ? 'VITE_CHURNSENSE_V2_PREVIEW=1 is set: the V2 Preview is built with `make v2-preview-build`, ' +
              'never into another folder. Unset it for the default build.'
          : 'build/v2-preview holds the V2 Preview build only: run `make v2-preview-build`.',
      );
    }
  },
};

export default defineConfig({
  plugins: [react(), v2PreviewBuildsApart],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    // Every asset is a file of its own: the CSP allows `data:` only for images.
    assetsInlineLimit: 0,
    rolldownOptions: {
      output: {
        // Third-party code in one chunk, so the app's own chunks are what the
        // wording check reads and a deploy of the app leaves it cached.
        codeSplitting: { groups: [{ name: 'vendor', test: /node_modules/ }] },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    fs: {
      // This folder, and the repository's synthetic fixtures: the batch page's
      // demo is imported from there with ?raw, which Vite admits only from an
      // allowed directory. Both are found from this file, never from where the
      // server was started, so the repository root - which holds data/raw and
      // artifacts - is never allowed.
      allow: [import.meta.dirname, resolve(import.meta.dirname, '../tests/fixtures')],
    },
    proxy,
  },
  // `vite preview`, for the V2 Preview build only (`make v2-preview-serve`). Stated
  // here rather than inherited from `server`, so neither can change the other.
  preview: {
    host: '127.0.0.1',
    proxy,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Scoped, as in the build: two modules' `.hideSm` must not meet in a test either.
    css: {
      // The tokens as written, for the contrast test (styles/contrast.test.ts); every
      // other stylesheet stays empty in a test, as before.
      include: [/\/src\/styles\/tokens\.css/],
      modules: { classNameStrategy: 'scoped' },
    },
    include: ['src/**/*.test.{ts,tsx}'],
    // A test that waits on a lazily loaded page and mocked responses can take past the
    // default 5 s when the machine is busy (load averages past 30 with another suite
    // running beside this one): 2 of 20 such runs failed that way and no other. A test
    // that hangs still fails, later.
    testTimeout: 15_000,
  },
});
