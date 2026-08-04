// SPDX-License-Identifier: AGPL-3.0-or-later
// Admin page: chatbot usage overview. Shows activity totals, top users,
// per-provider token splits, and per-tool call stats with error rates.

import { useState } from 'react';

import { useApiQuery } from '@/api/hooks';
import type {
  AdminUsageResponse,
  AdminUsageToolCall,
} from '@/api/chat';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { TimeRangeGroup } from '@/components/ui/TimeRangeGroup/TimeRangeGroup';
import styles from './ChatbotUsageTab.module.css';

const TIME_OPTIONS = [
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
  { value: '90d', label: '90d' },
];

function rangeToDays(v: string): number {
  switch (v) {
    case '24h': return 1;
    case '7d': return 7;
    case '30d': return 30;
    case '90d': return 90;
    default: return 7;
  }
}

function daysToRange(d: number): string {
  if (d === 1) return '24h';
  return `${d}d`;
}

function formatNumber(n: number | null | undefined): string {
  if (n == null) return '—';
  return n.toLocaleString();
}

function errorRate(tc: AdminUsageToolCall): string {
  if (tc.count === 0) return '0%';
  const bad = tc.error_count + tc.timeout_count;
  return `${((bad / tc.count) * 100).toFixed(1)}%`;
}

export function ChatbotUsageTab() {
  const [range, setRange] = useState<string>('7d');
  const days = rangeToDays(range);

  const { data, isLoading, error } = useApiQuery<AdminUsageResponse>(
    ['chat', 'admin', 'usage', days],
    '/chat/admin/usage',
    { params: { days } },
  );

  if (isLoading && !data) return <LoadingSpinner />;
  if (error) {
    return (
      <div className={styles.errorBox}>
        Failed to load chatbot usage: {String(error)}
      </div>
    );
  }
  if (!data) return null;

  const { totals, top_users, by_provider, tool_calls } = data;

  return (
    <div className={styles.page}>
      <div className={styles.topBar}>
        <TimeRangeGroup
          value={range}
          onChange={setRange}
          options={TIME_OPTIONS}
        />
      </div>

      <div className={styles.usageCard}>
        {/* Inline stats strip — pipe-separated key:value pairs, same
            convention as the users page. */}
        <div className={styles.statsLine}>
          {[
            { label: 'Messages', value: totals.messages },
            { label: 'Sessions', value: totals.sessions },
            { label: 'Active users', value: totals.active_users },
            { label: 'Input tokens', value: totals.tokens_input },
            { label: 'Output tokens', value: totals.tokens_output },
          ].map((s, i) => (
            <span key={s.label} className={styles.statItem}>
              {i > 0 && <span className={styles.statSep} aria-hidden>|</span>}
              <span className={styles.statKey}>{s.label}:</span>
              <span className={styles.statValue}>{formatNumber(s.value)}</span>
            </span>
          ))}
        </div>

        <Section title="Top users">
          <StatTable
            emptyText="No activity in this window."
            headers={[
              { label: 'Username' },
              { label: 'Messages', num: true },
            ]}
            rows={top_users.map((u) => ({
              key: u.user_id,
              cells: [
                u.username ?? u.user_id,
                { value: formatNumber(u.messages), num: true },
              ],
            }))}
          />
        </Section>

        <Section title="Tokens by provider">
          <StatTable
            emptyText="No provider activity."
            headers={[
              { label: 'Provider' },
              { label: 'Model' },
              { label: 'Input', num: true },
              { label: 'Output', num: true },
              { label: 'Messages', num: true },
            ]}
            rows={by_provider.map((r) => ({
              key: `${r.provider}|${r.model}`,
              cells: [
                r.provider,
                r.model,
                { value: formatNumber(r.input_tokens), num: true },
                { value: formatNumber(r.output_tokens), num: true },
                { value: formatNumber(r.messages), num: true },
              ],
            }))}
          />
        </Section>

        <Section title="Tool calls">
          <StatTable
            emptyText="No tool calls in this window."
            headers={[
              { label: 'Tool' },
              { label: 'Calls', num: true },
              { label: 'Errors', num: true },
              { label: 'Timeouts', num: true },
              { label: 'Denied', num: true },
              { label: 'Err rate', num: true },
              { label: 'Avg ms', num: true },
            ]}
            rows={tool_calls.map((tc) => ({
              key: tc.tool_name,
              cells: [
                tc.tool_name,
                { value: formatNumber(tc.count), num: true },
                { value: formatNumber(tc.error_count), num: true },
                { value: formatNumber(tc.timeout_count), num: true },
                { value: formatNumber(tc.denied_count), num: true },
                { value: errorRate(tc), num: true },
                { value: formatNumber(tc.avg_duration_ms), num: true },
              ],
            }))}
          />
        </Section>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>{title}</h3>
      {children}
    </section>
  );
}

type StatCellObj = { value: string | number; num?: boolean };
type StatCell = string | number | StatCellObj;

function isCellObj(c: StatCell): c is StatCellObj {
  return typeof c === 'object' && c !== null;
}

interface StatTableProps {
  emptyText: string;
  headers: { label: string; num?: boolean }[];
  rows: { key: string; cells: StatCell[] }[];
}

function StatTable({ emptyText, headers, rows }: StatTableProps) {
  if (rows.length === 0) {
    return <div className={styles.empty}>{emptyText}</div>;
  }
  return (
    <table className={styles.table}>
      <thead>
        <tr>
          {headers.map((h) => (
            <th key={h.label} className={h.num ? styles.num : undefined}>
              {h.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            {r.cells.map((c, i) => {
              const value = isCellObj(c) ? c.value : c;
              const num = isCellObj(c) && c.num;
              return (
                <td key={i} className={num ? styles.num : undefined}>
                  {value}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

