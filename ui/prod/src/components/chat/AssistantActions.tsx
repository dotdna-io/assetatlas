// SPDX-License-Identifier: AGPL-3.0-or-later
import { assetUrl } from '../../basePath';
import styles from './AssistantActions.module.css';

interface RecordedToolCall {
  id?: string;
  name?: string;
  arguments?: unknown;
}

interface AssistantActionsProps {
  messageId: string;
  toolCalls?: unknown;
  /** If provided, a regenerate button re-submits the preceding user turn
   *  as a new turn. Omitted when no preceding user message exists. */
  onRegenerate?: () => void;
}

const EXPLORABLE_TOOLS = new Set(['list_assets', 'aggregate_assets']);

function firstExplorableCall(raw: unknown): RecordedToolCall | null {
  if (!Array.isArray(raw)) return null;
  for (const tc of raw as RecordedToolCall[]) {
    if (tc && typeof tc === 'object' && tc.name &&
        EXPLORABLE_TOOLS.has(tc.name)) {
      return tc;
    }
  }
  return null;
}

/**
 * Best-effort conversion of a tool call's `filters`/`search` arguments into
 * URL params that ExplorerPage understands. Explorer reads `?search` as a
 * free-text query (parsed into keyword + structured filters) and `?filters`
 * as a JSON object `{field: [values]}` of column filters. We map `eq` filters
 * from the MCP tool (array of `{field, op, value}`) into that shape; anything
 * more complex falls back to `?search` only.
 */
export function buildExplorerUrl(args: unknown): string {
  const params = new URLSearchParams();
  const a = (args ?? {}) as Record<string, unknown>;

  if (typeof a.search === 'string' && a.search.trim()) {
    params.set('search', a.search.trim());
  }

  const rawFilters = a.filters;
  const parsedFilters = typeof rawFilters === 'string'
    ? safeJsonArray(rawFilters)
    : (Array.isArray(rawFilters) ? rawFilters : null);

  if (parsedFilters) {
    const columnFilters: Record<string, string[]> = {};
    for (const f of parsedFilters) {
      if (!f || typeof f !== 'object') continue;
      const field = (f as any).field;
      const op = (f as any).op;
      const value = (f as any).value;
      if (op !== 'eq' || !field || value === undefined || value === null) continue;
      const key = String(field);
      (columnFilters[key] ??= []).push(String(value));
    }
    if (Object.keys(columnFilters).length > 0) {
      params.set('filters', JSON.stringify(columnFilters));
    }
  }

  const qs = params.toString();
  return assetUrl(`/explorer${qs ? `?${qs}` : ''}`);
}

function safeJsonArray(s: string): unknown[] | null {
  try {
    const parsed = JSON.parse(s);
    return Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function AssistantActions({
  toolCalls,
  onRegenerate,
}: AssistantActionsProps) {
  const explorable = firstExplorableCall(toolCalls);
  if (!explorable && !onRegenerate) return null;

  const openInExplorer = () => {
    if (!explorable) return;
    const url = buildExplorerUrl(explorable.arguments);
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  return (
    <div className={styles.row}>
      {onRegenerate && (
        <button
          type="button"
          className={styles.iconBtn}
          onClick={onRegenerate}
          title="Ask this question again"
          aria-label="Regenerate answer"
        >
          ↻ Regenerate
        </button>
      )}
      {explorable && (
        <button
          type="button"
          className={`${styles.iconBtn} ${styles.openBtn}`}
          onClick={openInExplorer}
          title="Open this search in Explorer"
        >
          Open in Explorer ↗
        </button>
      )}
    </div>
  );
}
