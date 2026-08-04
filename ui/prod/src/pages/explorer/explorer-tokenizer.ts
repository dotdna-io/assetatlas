// SPDX-License-Identifier: AGPL-3.0-or-later
// Quote-aware tokenizer for Explorer search queries — pure TypeScript, zero
// React/DOM dependencies (same layer as explorer-query-parser.ts).

/**
 * A whitespace-separated query token. Quoted spans (single or double) are kept
 * whole even when they contain spaces: `text` is the inner content (no quotes)
 * and `start`/`end` (end exclusive) span the quotes in the original string.
 */
export interface QueryToken {
  text: string;
  start: number;
  end: number;
  quoted: boolean;
}

/**
 * Split a query into quote-aware, position-bearing tokens. A single token's
 * `text` is already unquoted, so callers resolve it against the field catalog
 * directly — no separate quote-stripping pass.
 *
 * Quotes are not escapable (matching `parseSearchQuery`): a quoted span ends at
 * the next same-quote character, and an unterminated quote runs to end-of-input.
 */
export function tokenizeQuery(s: string): QueryToken[] {
  const tokens: QueryToken[] = [];
  let i = 0;
  while (i < s.length) {
    if (/\s/.test(s[i])) {
      i++;
      continue;
    }
    const start = i;
    const ch = s[i];
    if (ch === '"' || ch === "'") {
      i++;
      let text = '';
      while (i < s.length && s[i] !== ch) {
        text += s[i];
        i++;
      }
      if (i < s.length) i++; // consume the closing quote
      tokens.push({ text, start, end: i, quoted: true });
    } else {
      let text = '';
      while (i < s.length && !/\s/.test(s[i])) {
        text += s[i];
        i++;
      }
      tokens.push({ text, start, end: i, quoted: false });
    }
  }
  return tokens;
}
