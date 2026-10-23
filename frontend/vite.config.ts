/// <reference types="vitest/config" />
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
    css: { modules: { classNameStrategy: 'non-scoped' } },
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
