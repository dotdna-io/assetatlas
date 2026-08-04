// SPDX-License-Identifier: AGPL-3.0-or-later
export type HelpCategory =
  | 'getting-started' | 'assets' | 'data-sources' | 'data-pipeline' | 'rules'
  | 'alerts' | 'reporting' | 'chatbot' | 'monitoring' | 'access-security' | 'system';

export const CATEGORY_ORDER = [
  'getting-started', 'assets', 'data-sources', 'data-pipeline', 'rules',
  'alerts', 'reporting', 'chatbot', 'monitoring', 'access-security', 'system',
] as const satisfies readonly HelpCategory[];

// Compile-time guard: if any HelpCategory is missing from CATEGORY_ORDER,
// or appears with the wrong spelling, tsc fails here with "Type 'never'
// is not assignable to type 'true'". No runtime cost.
type _CoversAllCategories<T extends readonly HelpCategory[]> =
  HelpCategory extends T[number] ? true : never;
const _: _CoversAllCategories<typeof CATEGORY_ORDER> = true;
void _;

export const CATEGORY_LABELS: Record<HelpCategory, string> = {
  'getting-started': 'Getting Started',
  'assets': 'Assets',
  'data-sources': 'Data Sources',
  'data-pipeline': 'Data Pipeline',
  'rules': 'Rules',
  'alerts': 'Alerts',
  'reporting': 'Reporting',
  'chatbot': 'Chatbot',
  'monitoring': 'Monitoring',
  'access-security': 'Access & Security',
  'system': 'System',
};

export interface HelpTopic {
  id: string;          // 'integrations' -> /help/integrations
  title: string;
  category: HelpCategory;
  file: string;        // basename in help/content, e.g. 'integrations.md'
  routes: string[];    // app pathname prefixes that resolve to this topic
  order: number;       // sort within category
}
