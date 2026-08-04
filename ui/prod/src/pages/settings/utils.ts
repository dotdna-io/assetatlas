// SPDX-License-Identifier: AGPL-3.0-or-later
export function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return '—';
  return new Date(ts).toLocaleString();
}
