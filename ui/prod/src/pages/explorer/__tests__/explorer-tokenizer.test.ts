// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, expect, it } from 'vitest';

import { tokenizeQuery } from '../explorer-tokenizer';

describe('tokenizeQuery', () => {
  it('splits unquoted tokens on whitespace with positions', () => {
    expect(tokenizeQuery('hostname = Dell')).toEqual([
      { text: 'hostname', start: 0, end: 8, quoted: false },
      { text: '=', start: 9, end: 10, quoted: false },
      { text: 'Dell', start: 11, end: 15, quoted: false },
    ]);
  });

  it('keeps a double-quoted span whole and drops the quotes from text', () => {
    expect(tokenizeQuery('"Data Source"')).toEqual([
      { text: 'Data Source', start: 0, end: 13, quoted: true },
    ]);
  });

  it('keeps a single-quoted span whole', () => {
    expect(tokenizeQuery("'Data Source'")).toEqual([
      { text: 'Data Source', start: 0, end: 13, quoted: true },
    ]);
  });

  it('tracks positions across a quoted field, operator, and value', () => {
    expect(tokenizeQuery('"Data Source" ~ Win')).toEqual([
      { text: 'Data Source', start: 0, end: 13, quoted: true },
      { text: '~', start: 14, end: 15, quoted: false },
      { text: 'Win', start: 16, end: 19, quoted: false },
    ]);
  });

  it('runs an unterminated quote to the end of the string', () => {
    expect(tokenizeQuery('"Data Sou')).toEqual([
      { text: 'Data Sou', start: 0, end: 9, quoted: true },
    ]);
  });

  it('treats an empty quoted span as an empty quoted token', () => {
    expect(tokenizeQuery('""')).toEqual([{ text: '', start: 0, end: 2, quoted: true }]);
  });

  it('does not treat \\" as an escaped quote — the quote still closes the span', () => {
    // Input is the 4 chars: " a \ "  — the backslash is literal and the second
    // quote closes the token (consistent with parseSearchQuery: no escapes).
    expect(tokenizeQuery('"a\\"')).toEqual([{ text: 'a\\', start: 0, end: 4, quoted: true }]);
  });

  it('collapses whitespace runs and ignores leading/trailing spaces', () => {
    expect(tokenizeQuery('  a   b  ')).toEqual([
      { text: 'a', start: 2, end: 3, quoted: false },
      { text: 'b', start: 6, end: 7, quoted: false },
    ]);
  });

  it('returns no tokens for empty or whitespace-only input', () => {
    expect(tokenizeQuery('')).toEqual([]);
    expect(tokenizeQuery('   ')).toEqual([]);
  });
});
