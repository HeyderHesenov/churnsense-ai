/// <reference types="vitest/config" />
import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The API the dev server forwards to: `make api` (or `make api API_PORT=8001`).
const apiPort = process.env.API_PORT ?? '8000';

export default defineConfig({
  plugins: [react()],
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
    proxy: {
      // changeOrigin stays false: the API sees the dev server's own host and
      // origin, which its same-origin check accepts like the built app's.
      '/api': { target: `http://127.0.0.1:${apiPort}`, changeOrigin: false },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Scoped, as in the build: two modules' `.hideSm` must not meet in a test either.
    css: { modules: { classNameStrategy: 'scoped' } },
    include: ['src/**/*.test.{ts,tsx}'],
    // A test that waits on a lazily loaded page and mocked responses can take past the
    // default 5 s when the machine is busy (load averages past 30 with another suite
    // running beside this one): 2 of 20 such runs failed that way and no other. A test
    // that hangs still fails, later.
    testTimeout: 15_000,
  },
});
