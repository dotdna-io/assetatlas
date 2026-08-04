// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';

vi.mock('../manifest', () => ({
  TOPICS: [
    { id: 'integrations', title: 'I', category: 'data-sources', file: 'i.md', routes: ['/integrations', '/integration-wizard'], order: 0 },
    { id: 'enrichment',  title: 'E', category: 'data-sources', file: 'e.md', routes: ['/settings/data/enrichment'], order: 1 },
    { id: 'pipeline',    title: 'P', category: 'data-pipeline', file: 'p.md', routes: ['/settings/data/pipeline'], order: 0 },
  ],
}));

import { resolveTopicId, helpHrefForPath } from '../helpResolver';

describe('helpResolver', () => {
  it('matches the longest route prefix', () => {
    expect(resolveTopicId('/settings/data/enrichment')).toBe('enrichment');
    expect(resolveTopicId('/integration-wizard?id=5')).toBe('integrations');
    expect(resolveTopicId('/integrations')).toBe('integrations');
  });
  it('returns null when nothing matches', () => {
    expect(resolveTopicId('/account')).toBeNull();
  });
  it('helpHrefForPath builds /help/<id> or /help', () => {
    expect(helpHrefForPath('/integrations')).toBe('/help/integrations');
    expect(helpHrefForPath('/account')).toBe('/help');
  });
});
