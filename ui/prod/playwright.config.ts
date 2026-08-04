import { defineConfig, devices } from '@playwright/test';
import { existsSync } from 'node:fs';

// Load gitignored ui/prod/.env (TEST_USERNAME/TEST_PASSWORD for the e2e
// tests). Node ≥20.12 built-in — deliberately no dotenv dep.
// npm scripts run with cwd = ui/prod, so '.env' resolves correctly.
if (existsSync('.env')) process.loadEnvFile('.env');

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  timeout: 30_000,
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
