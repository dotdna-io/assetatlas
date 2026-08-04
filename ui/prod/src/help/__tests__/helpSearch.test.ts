// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import type { HelpTopic } from '../types';
import { buildSearchIndex, searchHelp } from '../helpSearch';

const TOPICS: HelpTopic[] = [
  { id: 'integrations', title: 'Connecting Data Sources', category: 'data-sources', file: 'i.md', routes: [], order: 0 },
  { id: 'oidc', title: 'OIDC Single Sign-On', category: 'access-security', file: 'o.md', routes: [], order: 0 },
];
const CONTENT: Record<string, string> = {
  integrations: '# Connecting\nUse the wizard to add a Splunk source.',
  oidc: '# OIDC\nConfigure the identity provider client secret.',
};

describe('helpSearch', () => {
  const idx = buildSearchIndex(TOPICS, (t) => CONTENT[t.id] ?? '');
  it('matches title case-insensitively', () => {
    expect(searchHelp(idx, 'data sources').map((t) => t.id)).toEqual(['integrations']);
  });
  it('matches body tokens', () => {
    expect(searchHelp(idx, 'splunk').map((t) => t.id)).toEqual(['integrations']);
  });
  it('empty query returns nothing', () => {
    expect(searchHelp(idx, '   ')).toEqual([]);
  });
});
