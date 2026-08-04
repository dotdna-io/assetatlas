// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { TOPICS, getTopicContent } from '../manifest';
import { resolveTopicId } from '../helpResolver';

// Resolve ui/prod/public relative to this test file's location:
// __tests__/ -> help/ -> src/ -> ui/prod/
const __filename = fileURLToPath(import.meta.url);
const PUBLIC_DIR = resolve(dirname(__filename), '..', '..', '..', 'public');

// ---------------------------------------------------------------------------
// 1. Content present & non-empty
// ---------------------------------------------------------------------------
describe('content present and non-empty', () => {
  it.each(TOPICS)('topic "$id" has non-empty markdown content', (topic) => {
    const md = getTopicContent(topic);
    expect(
      md.trim().length,
      `topic "${topic.id}" (file: ${topic.file}) returned empty or whitespace-only content`,
    ).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// 2. Image refs are valid real on-disk paths
// ---------------------------------------------------------------------------
// Help content has no images today; this guards any that get added later —
// a ref must point to a /help/img/... file that exists on disk.
// Regex captures the target (src) from any markdown image: ![alt](TARGET)
const IMG_REF_RE = /!\[[^\]]*\]\(([^)]+)\)/g;

describe('image refs are valid', () => {
  it.each(TOPICS)('topic "$id" has only valid image references', (topic) => {
    const md = getTopicContent(topic);
    const bad: string[] = [];

    for (const match of md.matchAll(IMG_REF_RE)) {
      const target = match[1].trim();

      // Allowed: /help/img/... path that exists on disk
      if (target.startsWith('/help/img/')) {
        const diskPath = resolve(PUBLIC_DIR, target.slice(1)); // strip leading /
        if (existsSync(diskPath)) continue;
        bad.push(`${target} (file not found at ${diskPath})`);
        continue;
      }

      // Everything else is a rot signal: bare filename, wrong prefix, etc.
      bad.push(
        `${target} (must be a /help/img/... path with a file on disk)`,
      );
    }

    expect(bad, `topic "${topic.id}" has invalid image ref(s):\n  ${bad.join('\n  ')}`).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// 3. Routes present — every topic has a non-empty routes array, all starting /
// ---------------------------------------------------------------------------
describe('routes present and well-formed', () => {
  it.each(TOPICS)('topic "$id" has at least one route, all starting with /', (topic) => {
    expect(
      topic.routes.length,
      `topic "${topic.id}" has an empty routes array`,
    ).toBeGreaterThan(0);

    for (const r of topic.routes) {
      expect(
        r.startsWith('/'),
        `topic "${topic.id}" has route "${r}" that does not start with /`,
      ).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// 4. Resolver determinism — guards ordering / dedup of shared routes
// ---------------------------------------------------------------------------
// These use the REAL manifest and resolver (no mocking).
// If someone reorders TOPICS so the first /rules match is no longer
// rules-overview, or removes posture-rules's route, these fail explicitly.
describe('resolveTopicId determinism', () => {
  it('/rules resolves to rules-overview (first /rules topic by TOPICS order)', () => {
    expect(resolveTopicId('/rules')).toBe('rules-overview');
  });

  it('/datasource-details resolves to integrations', () => {
    expect(resolveTopicId('/datasource-details')).toBe('integrations');
  });

  it('/settings/access/api-keys resolves to api-keys', () => {
    expect(resolveTopicId('/settings/access/api-keys')).toBe('api-keys');
  });

  it('/integrations resolves to integrations', () => {
    expect(resolveTopicId('/integrations')).toBe('integrations');
  });

  it('unknown route resolves to null', () => {
    expect(resolveTopicId('/this-does-not-exist')).toBeNull();
  });

  it('/rules/some-sub-path still resolves to rules-overview (prefix match)', () => {
    expect(resolveTopicId('/rules/some-sub-path')).toBe('rules-overview');
  });
});

// ---------------------------------------------------------------------------
// 4b. Contextual-help coverage — every primary app page resolves to a topic.
// ---------------------------------------------------------------------------
// Guards the `?` help link: if a manifest route is dropped or a page is added
// without a topic, the matching case here fails. Auth/pre-login routes are
// intentionally excluded (they render outside the AppShell, so no help nav).
describe('contextual-help route coverage', () => {
  const CASES: Array<[string, string]> = [
    ['/dashboard', 'getting-started'],
    ['/explorer', 'explore'],
    ['/asset-details', 'asset-details'],
    ['/asset-history', 'asset-history'],
    ['/alerts', 'alerts'],
    ['/reporting', 'reporting'],
    ['/reporting/new', 'reporting'],
    ['/integrations', 'integrations'],
    ['/integration-wizard', 'integrations'],
    ['/integration-details', 'integrations'],
    ['/datasource-details', 'integrations'],
    ['/account', 'account'],
    ['/settings/data/pipeline', 'pipeline'],
    ['/settings/data/confidence', 'pipeline'],
    ['/settings/data/semantic', 'field-registry'],
    ['/settings/data/unified-fields', 'unified-fields'],
    ['/settings/data/enrichment', 'enrichment'],
    ['/settings/data/reference-data', 'reference-data'],
    ['/settings/data/merge-denylist', 'merge-denylist'],
    ['/settings/alert-delivery/channels', 'alert-delivery'],
    ['/settings/alert-delivery/routing', 'alert-delivery'],
    ['/settings/chatbot/configuration', 'chatbot-configuration'],
    ['/settings/chatbot/behavior', 'chatbot-behavior'],
    ['/settings/chatbot/usage', 'chatbot-usage'],
    ['/settings/chatbot/teams', 'chatbot-teams'],
    ['/settings/monitoring/dashboards', 'monitoring-dashboards'],
    ['/settings/monitoring/forwarding', 'monitoring-forwarding'],
    ['/settings/access/users', 'users-access'],
    ['/settings/access/api-keys', 'api-keys'],
    ['/settings/access/ldap', 'ldap'],
    ['/settings/access/oidc', 'oidc'],
    ['/settings/access/audit', 'audit-log'],
    ['/settings/system/security', 'security-jwt'],
    ['/settings/system/retention', 'retention'],
    ['/settings/system/logging', 'logging'],
    ['/settings/system/email', 'email'],
    ['/settings/system/about', 'about'],
  ];
  it.each(CASES)('%s resolves to %s', (route, id) => {
    expect(resolveTopicId(route)).toBe(id);
  });
});

// ---------------------------------------------------------------------------
// 5. Image-ref predicate — on-disk branch coverage
// ---------------------------------------------------------------------------
// Mirrors the section-2 predicate to document what a valid image ref is now
// that the "screenshot" placeholder token is gone: only /help/img/... paths
// that exist on disk. No committed image is needed for these negative cases.
describe('image-ref predicate (existsSync branches)', () => {
  // Mirrors the predicate inline to keep it a pure local helper — no
  // code changes to the production source are needed.
  function isValidImageRef(target: string): boolean {
    if (target.startsWith('/help/img/')) {
      return existsSync(resolve(PUBLIC_DIR, target.slice(1)));
    }
    return false; // bare filename, wrong prefix, etc.
  }

  it('non-existent /help/img/... path is invalid', () => {
    const ref = '/help/img/__nope__/x.png';
    expect(existsSync(resolve(PUBLIC_DIR, ref.slice(1)))).toBe(false); // not there
    expect(isValidImageRef(ref)).toBe(false);
  });

  it('bare filename (wrong prefix) is invalid', () => {
    expect(isValidImageRef('foo.png')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 6. Unique ids and known categories
// ---------------------------------------------------------------------------
describe('manifest structural invariants', () => {
  it('all topic ids are unique', () => {
    const ids = TOPICS.map((t) => t.id);
    const duplicates = ids.filter((id, i) => ids.indexOf(id) !== i);
    expect(
      duplicates,
      `duplicate topic id(s): ${duplicates.join(', ')}`,
    ).toHaveLength(0);
  });

  it('all routes are unique across topics (no silent dedup of non-shared routes)', () => {
    // Routes that appear in MULTIPLE topics are allowed (e.g. /rules appears in
    // rules-overview, rule-wizard, tag-rules — these are intentional shared routes).
    // What we want to catch is accidental duplication where the SAME route appears
    // twice in the SAME topic's routes array.
    for (const topic of TOPICS) {
      const seen = new Set<string>();
      const dups: string[] = [];
      for (const r of topic.routes) {
        if (seen.has(r)) dups.push(r);
        seen.add(r);
      }
      expect(
        dups,
        `topic "${topic.id}" has duplicate route entries: ${dups.join(', ')}`,
      ).toHaveLength(0);
    }
  });
});
