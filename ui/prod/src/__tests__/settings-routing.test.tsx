// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';

// Probe component that surfaces the current pathname for assertion.
function PathProbe() {
  const loc = useLocation();
  return <div data-testid="path">{loc.pathname}</div>;
}

// Minimal copy of the redirect block under test. We do NOT mount the full App —
// it depends on auth context, query client, etc. Instead we assert that the
// redirect routes themselves resolve as designed.
//
// Note: We need to test that the routes structure in App.tsx matches what we expect.
// The key is that Navigate with 'replace' will change the location in MemoryRouter,
// which will then be matched against the route tree again, eventually landing on
// a PathProbe that shows the final pathname.
import { Outlet } from 'react-router-dom';

function GroupShell() {
  return <Outlet />;
}

function RedirectsHarness() {
  return (
    <Routes>
      {/* Settings group + leaf shells (just probes — we only care about the redirect target) */}
      <Route path="/settings" element={<Outlet />}>
        <Route index element={<Navigate to="system" replace />} />
        <Route path="data" element={<GroupShell />}>
          <Route index element={<Navigate to="pipeline" replace />} />
          <Route path="pipeline" element={<PathProbe />} />
          <Route path="fields" element={<PathProbe />} />
          <Route path="enrichment" element={<PathProbe />} />
        </Route>
        <Route path="system" element={<GroupShell />}>
          <Route index element={<Navigate to="retention" replace />} />
          <Route path="retention" element={<PathProbe />} />
          <Route path="security" element={<PathProbe />} />
          <Route path="logging" element={<PathProbe />} />
          <Route path="email" element={<PathProbe />} />
          <Route path="about" element={<PathProbe />} />
        </Route>
        <Route path="posture" element={<PathProbe />} />
        <Route path="access" element={<GroupShell />}>
          <Route index element={<Navigate to="users" replace />} />
          <Route path="users" element={<PathProbe />} />
          <Route path="api-keys" element={<PathProbe />} />
          <Route path="ldap" element={<PathProbe />} />
          <Route path="oidc" element={<PathProbe />} />
          <Route path="audit" element={<PathProbe />} />
        </Route>
        <Route path="chatbot" element={<GroupShell />}>
          <Route index element={<Navigate to="configuration" replace />} />
          <Route path="configuration" element={<PathProbe />} />
          <Route path="usage" element={<PathProbe />} />
          <Route path="teams" element={<PathProbe />} />
        </Route>
        <Route path="monitoring" element={<GroupShell />}>
          <Route index element={<Navigate to="forwarding" replace />} />
          <Route path="forwarding" element={<PathProbe />} />
          <Route path="dashboards" element={<PathProbe />} />
        </Route>

        {/* Backward-compat */}
        <Route path="asset-generation" element={<Navigate to="/settings/data/pipeline" replace />} />
        <Route path="posture-rules" element={<Navigate to="/settings/posture" replace />} />
        <Route path="field-registry" element={<Navigate to="/settings/data/fields" replace />} />
        <Route path="data-retention" element={<Navigate to="/settings/system/retention" replace />} />
        <Route path="security" element={<Navigate to="/settings/system/security" replace />} />
        <Route path="logging" element={<Navigate to="/settings/system/logging" replace />} />
        <Route path="enrichment" element={<Navigate to="/settings/data/enrichment" replace />} />
        <Route path="email" element={<Navigate to="/settings/system/email" replace />} />
        <Route path="api-keys" element={<Navigate to="/settings/access/api-keys" replace />} />
        <Route path="audit-log" element={<Navigate to="/settings/access/audit" replace />} />
        <Route path="chatbot-usage" element={<Navigate to="/settings/chatbot/usage" replace />} />
        <Route path="about" element={<Navigate to="/settings/system/about" replace />} />
        <Route path="data/posture" element={<Navigate to="/settings/posture" replace />} />
        <Route path="users" element={<Navigate to="/settings/access/users" replace />} />
        <Route path="users/list" element={<Navigate to="/settings/access/users" replace />} />
        <Route path="users/api-keys" element={<Navigate to="/settings/access/api-keys" replace />} />
        <Route path="users/audit" element={<Navigate to="/settings/access/audit" replace />} />
        <Route path="system/ldap" element={<Navigate to="/settings/access/ldap" replace />} />
        <Route path="system/oidc" element={<Navigate to="/settings/access/oidc" replace />} />
      </Route>
      <Route path="/pipeline" element={<Navigate to="/settings/data/pipeline" replace />} />
      <Route path="/audit-log" element={<Navigate to="/settings/access/audit" replace />} />
      <Route path="/monitoring" element={<Navigate to="/settings/monitoring/dashboards" replace />} />
    </Routes>
  );
}

describe('settings routing redirects', () => {
  const cases: [string, string][] = [
    // index defaults
    ['/settings', '/settings/system/retention'],
    ['/settings/data', '/settings/data/pipeline'],
    ['/settings/system', '/settings/system/retention'],
    ['/settings/access', '/settings/access/users'],
    ['/settings/chatbot', '/settings/chatbot/configuration'],
    ['/settings/chatbot/teams', '/settings/chatbot/teams'],
    ['/settings/monitoring', '/settings/monitoring/forwarding'],
    // legacy leaf URLs
    ['/settings/asset-generation', '/settings/data/pipeline'],
    ['/settings/posture-rules', '/settings/posture'],
    ['/settings/field-registry', '/settings/data/fields'],
    ['/settings/data-retention', '/settings/system/retention'],
    ['/settings/security', '/settings/system/security'],
    ['/settings/logging', '/settings/system/logging'],
    ['/settings/enrichment', '/settings/data/enrichment'],
    ['/settings/email', '/settings/system/email'],
    ['/settings/api-keys', '/settings/access/api-keys'],
    ['/settings/audit-log', '/settings/access/audit'],
    ['/settings/chatbot-usage', '/settings/chatbot/usage'],
    ['/settings/about', '/settings/system/about'],
    // moved sub-tabs
    ['/settings/data/posture', '/settings/posture'],
    ['/settings/users', '/settings/access/users'],
    ['/settings/users/list', '/settings/access/users'],
    ['/settings/users/api-keys', '/settings/access/api-keys'],
    ['/settings/users/audit', '/settings/access/audit'],
    ['/settings/system/ldap', '/settings/access/ldap'],
    ['/settings/system/oidc', '/settings/access/oidc'],
    // top-level shortcuts
    ['/pipeline', '/settings/data/pipeline'],
    ['/audit-log', '/settings/access/audit'],
    ['/monitoring', '/settings/monitoring/dashboards'],
  ];

  it.each(cases)('redirects %s -> %s', async (from, expected) => {
    render(
      <MemoryRouter initialEntries={[from]}>
        <RedirectsHarness />
      </MemoryRouter>,
    );
    await waitFor(() => {
      const probes = screen.queryAllByTestId('path');
      // The leaf probe is the last one rendered (innermost outlet).
      expect(probes[probes.length - 1]?.textContent).toBe(expected);
    });
  });
});
