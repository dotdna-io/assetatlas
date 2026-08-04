// SPDX-License-Identifier: AGPL-3.0-or-later
/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// A CSS module referenced only via `composes: … from '…'` (shared.module.css)
// is read inside each consumer's postcss transform and never enters Vite's
// module graph — editing it maps to zero modules, so no HMR fires and the
// consumers' cached transforms keep serving stale styles even to fresh page
// loads; only a server restart flushed it. When a changed .module.css matches
// no modules, invalidate every CSS module and force a full reload.
const composesSourceHmr: Plugin = {
  name: 'composes-source-hmr',
  handleHotUpdate({ file, server, modules }) {
    if (!file.endsWith('.module.css') || modules.length > 0) return;
    for (const mod of server.moduleGraph.idToModuleMap.values()) {
      if (mod.file?.endsWith('.module.css')) server.moduleGraph.invalidateModule(mod);
    }
    server.ws.send({ type: 'full-reload' });
    return [];
  },
};

export default defineConfig({
  plugins: [react(), composesSourceHmr],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 3000,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
      // Teams bot link flow: /teams/link and /teams/linked are
      // server-rendered Flask routes that need to share localStorage
      // (access_token) with the React UI, so we proxy them through
      // the Vite origin.
      '/teams': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
    },
  },
  publicDir: 'public',
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    exclude: ['e2e/**', 'node_modules/**'],
    setupFiles: ['src/test-setup.ts'],
  },
});
