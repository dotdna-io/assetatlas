// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';

import type { ToolCallInfo } from './hooks/useSSEStream';
import styles from './ToolActivity.module.css';

export const STATUS_SYMBOL: Record<ToolCallInfo['status'], string> = {
  running: '…',
  ok: '✓',
  error: '✗',
};

/** Short human label for the tools used, e.g. "list_assets, get_asset" or
 *  "list_assets ×2" when one tool was called repeatedly. Collapses long
 *  chains into a summary to keep the header one line. */
export function buildToolLabel(items: Array<{ name: string }>): string {
  if (items.length === 0) return 'tools';
  const counts = new Map<string, number>();
  for (const c of items) counts.set(c.name, (counts.get(c.name) ?? 0) + 1);
  const parts: string[] = [];
  for (const [name, n] of counts) {
    parts.push(n > 1 ? `${name} ×${n}` : name);
  }
  if (parts.length <= 2) return parts.join(', ');
  return `${parts.slice(0, 2).join(', ')} +${parts.length - 2}`;
}

/** Unified live tool-activity chip for the streaming bubble: one collapsible
 *  toggle summarising the whole turn's tool calls — "Using …" with a spinner
 *  while any call is still running, "Used …" once all have settled. Expands
 *  to one row per call (status symbol + name); live calls carry no
 *  arguments, so unlike ToolCallDetails there is nothing else to show. */
export function ToolActivity({ calls }: { calls: ToolCallInfo[] }) {
  const [open, setOpen] = useState(false);
  if (calls.length === 0) return null;
  const running = calls.some((c) => c.status === 'running');
  return (
    <>
      <button
        type="button"
        className={styles.toggle}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={styles.caret}>{open ? '▾' : '▸'}</span>
        {running && <span className={styles.spinner} aria-hidden="true" />}
        <span>
          {running ? 'Using ' : 'Used '}
          {buildToolLabel(calls)}
        </span>
      </button>
      {open && (
        <div className={styles.details}>
          {calls.map((c, i) => (
            <div key={c.id || i} className={styles.detail}>
              <span className={styles.detailStatus} aria-hidden="true">
                {STATUS_SYMBOL[c.status]}
              </span>
              <span className={styles.detailName}>{c.name}</span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
