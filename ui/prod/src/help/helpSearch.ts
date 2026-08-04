// SPDX-License-Identifier: AGPL-3.0-or-later
import type { HelpTopic } from './types';

export interface SearchEntry { topic: HelpTopic; haystack: string; }
export type SearchIndex = SearchEntry[];

export function buildSearchIndex(
  topics: HelpTopic[],
  getContent: (t: HelpTopic) => string,
): SearchIndex {
  return topics.map((topic) => ({
    topic,
    haystack: `${topic.title}\n${getContent(topic)}`.toLowerCase(),
  }));
}

/** Case-insensitive: every whitespace-separated token in the query must
 *  appear as a substring of the topic's title+body. No fuzzy library. */
export function searchHelp(index: SearchIndex, query: string): HelpTopic[] {
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return [];
  return index
    .filter((e) => tokens.every((tok) => e.haystack.includes(tok)))
    .map((e) => e.topic);
}
