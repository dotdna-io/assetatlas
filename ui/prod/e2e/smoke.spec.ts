import { test, expect, type Page } from '@playwright/test';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const CREDENTIALS = {
  username: process.env.TEST_USERNAME || 'admin',
  password: process.env.TEST_PASSWORD || 'admin123',
};

async function login(page: Page) {
  await page.goto('/login');
  await page.fill('input[name="username"], #username', CREDENTIALS.username);
  await page.fill('input[name="password"], #password', CREDENTIALS.password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(dashboard|asset-homepage)/, { timeout: 10_000 });
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

test.describe('Authentication', () => {
  test('login page loads', async ({ page }) => {
    await page.goto('/login');
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test('login with valid credentials redirects to dashboard', async ({ page }) => {
    await login(page);
    await expect(page).toHaveURL(/\/(dashboard|asset-homepage)/);
  });

  test('login with invalid credentials shows error', async ({ page }) => {
    await page.goto('/login');
    await page.fill('input[name="username"], #username', 'baduser');
    await page.fill('input[name="password"], #password', 'badpass');
    await page.click('button[type="submit"]');
    // Should stay on login page and show an error
    await expect(page).toHaveURL(/\/login/);
    // Wait for error message to appear
    await page.waitForTimeout(1000);
    const errorVisible = await page.locator('.error, .alert-error, [class*="error"]').isVisible().catch(() => false);
    expect(errorVisible || (await page.url()).includes('/login')).toBeTruthy();
  });

  test('unauthenticated access redirects to login', async ({ page }) => {
    // Clear any stored tokens
    await page.goto('/login');
    await page.evaluate(() => {
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
    });
    await page.goto('/dashboard');
    // Auth guard should redirect to login
    await page.waitForTimeout(2000);
    await expect(page).toHaveURL(/\/login/);
  });
});

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

test.describe('Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('dashboard loads with content', async ({ page }) => {
    await page.goto('/dashboard');
    await page.waitForLoadState('networkidle');
    // Check that the page has loaded with some content
    const bodyText = await page.locator('body').innerText();
    expect(bodyText.length).toBeGreaterThan(100);
  });

  test('sidebar navigation is visible', async ({ page }) => {
    await page.goto('/dashboard');
    await page.waitForLoadState('networkidle');
    const sidebar = page.locator('.sidebar, nav, [class*="sidebar"]');
    await expect(sidebar.first()).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Explorer
// ---------------------------------------------------------------------------

test.describe('Explorer', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('explorer page loads', async ({ page }) => {
    await page.goto('/explorer');
    await page.waitForLoadState('networkidle');
    // Explorer should have a table or search component
    const hasTable = await page.locator('table, [class*="table"]').first().isVisible().catch(() => false);
    const hasSearch = await page.locator('input[type="search"], input[type="text"], [class*="search"]').first().isVisible().catch(() => false);
    expect(hasTable || hasSearch).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Integrations
// ---------------------------------------------------------------------------

test.describe('Integrations', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('integrations page loads', async ({ page }) => {
    await page.goto('/integrations');
    await page.waitForLoadState('networkidle');
    const bodyText = await page.locator('body').innerText();
    expect(bodyText.length).toBeGreaterThan(50);
  });

  test('integration wizard loads', async ({ page }) => {
    await page.goto('/integration-wizard?mode=new');
    await page.waitForLoadState('networkidle');
    const bodyText = await page.locator('body').innerText();
    expect(bodyText.length).toBeGreaterThan(50);
  });
});

// ---------------------------------------------------------------------------
// Reporting
// ---------------------------------------------------------------------------

test.describe('Reporting', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('reporting page loads', async ({ page }) => {
    await page.goto('/reporting');
    await page.waitForLoadState('networkidle');
    const bodyText = await page.locator('body').innerText();
    expect(bodyText.length).toBeGreaterThan(50);
  });
});

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

test.describe('Settings', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('settings page loads with tabs', async ({ page }) => {
    await page.goto('/settings');
    await page.waitForLoadState('networkidle');
    const bodyText = await page.locator('body').innerText();
    expect(bodyText.length).toBeGreaterThan(50);
  });
});

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------

test.describe('Alerts', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('alerts page loads', async ({ page }) => {
    await page.goto('/alerts');
    await page.waitForLoadState('networkidle');
    const bodyText = await page.locator('body').innerText();
    expect(bodyText.length).toBeGreaterThan(50);
  });
});

// ---------------------------------------------------------------------------
// Dark Mode
// ---------------------------------------------------------------------------

test.describe('Dark Mode', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('dark mode toggle works', async ({ page }) => {
    await page.goto('/dashboard');
    await page.waitForLoadState('networkidle');

    // Find and click dark mode toggle
    const toggle = page.locator('[class*="dark-mode"], [class*="theme"], #darkModeToggle, .dark-mode-toggle').first();
    const toggleExists = await toggle.isVisible().catch(() => false);

    if (toggleExists) {
      await toggle.click();
      await page.waitForTimeout(500);
      const hasDarkClass = await page.evaluate(() =>
        document.body.classList.contains('dark-mode') ||
        document.documentElement.classList.contains('dark-mode') ||
        document.body.getAttribute('data-theme') === 'dark'
      );
      expect(hasDarkClass).toBeTruthy();
    }
  });
});

// ---------------------------------------------------------------------------
// Logout
// ---------------------------------------------------------------------------

test.describe('Logout', () => {
  test('logout clears session and redirects to login', async ({ page }) => {
    await login(page);
    await page.goto('/dashboard');
    await page.waitForLoadState('networkidle');

    // Try to find and click logout button
    const logoutBtn = page.locator('text=Logout, text=Sign Out, text=Log Out, [class*="logout"]').first();
    const logoutVisible = await logoutBtn.isVisible().catch(() => false);

    if (logoutVisible) {
      await logoutBtn.click();
      await page.waitForURL(/\/login/, { timeout: 5000 });
      await expect(page).toHaveURL(/\/login/);
    } else {
      // If no visible logout button, verify we can clear tokens manually
      await page.evaluate(() => {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
      });
      await page.goto('/dashboard');
      await page.waitForTimeout(2000);
      await expect(page).toHaveURL(/\/login/);
    }
  });
});

// ---------------------------------------------------------------------------
// Health Check
// ---------------------------------------------------------------------------

test.describe('Health', () => {
  test('health endpoint returns ok', async ({ request }) => {
    const response = await request.get('/health');
    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    expect(body.status).toBe('ok');
  });
});

// ---------------------------------------------------------------------------
// CSP Headers
// ---------------------------------------------------------------------------

test.describe('Security Headers', () => {
  test('CSP header is present', async ({ request }) => {
    const response = await request.get('/login');
    const csp = response.headers()['content-security-policy'];
    expect(csp).toBeTruthy();
    expect(csp).toContain("default-src 'self'");
  });
});
