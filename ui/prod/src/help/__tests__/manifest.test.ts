// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { TOPICS, getTopicById, getTopicContent } from '../manifest';
import { CATEGORY_ORDER } from '../types';

describe('help manifest', () => {
  it('has unique ids and known categories', () => {
    const ids = TOPICS.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const t of TOPICS) expect(CATEGORY_ORDER).toContain(t.category);
  });

  it('every topic resolves to bundled markdown content', () => {
    for (const t of TOPICS) {
      const md = getTopicContent(t);
      expect(typeof md).toBe('string');
      expect(md.length).toBeGreaterThan(0);
    }
  });

  it('getTopicById returns the topic or undefined', () => {
    const first = TOPICS[0];
    expect(getTopicById(first.id)).toEqual(first);
    expect(getTopicById('__nope__')).toBeUndefined();
  });
});
