// SPDX-License-Identifier: AGPL-3.0-or-later
import { TOPICS } from './manifest';

/** Resolve an app pathname to a help topic id by longest matching
 *  route prefix. Query string is ignored. */
export function resolveTopicId(pathname: string): string | null {
  const path = pathname.split('?')[0];
  let best: { id: string; len: number } | null = null;
  for (const t of TOPICS) {
    for (const r of t.routes) {
      // Exact match, or prefix match on a path-segment boundary so
      // `/integrations` matches `/integrations/9` but not `/integrationsX`.
      if (path === r || path.startsWith(r + '/')) {
        if (!best || r.length > best.len) best = { id: t.id, len: r.length };
      }
    }
  }
  return best ? best.id : null;
}

export function helpHrefForPath(pathname: string): string {
  const id = resolveTopicId(pathname);
  return id ? `/help/${id}` : '/help';
}
