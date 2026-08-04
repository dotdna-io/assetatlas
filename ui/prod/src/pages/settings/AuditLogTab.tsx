// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/AuditLogTab.tsx
import React, { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuditLog } from '@/api/settings';
import type { AuditLogEntry } from '@/api/settings';
import { Button } from '@/components/ui/Button';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { TimeRangeGroup } from '@/components/ui/TimeRangeGroup/TimeRangeGroup';
import { formatTimestamp } from './utils';
import styles from './AuditLogTab.module.css';

function formatJson(data: any): string {
  if (data === null || data === undefined) return '(none)';
  if (typeof data === 'string') {
    try {
      return JSON.stringify(JSON.parse(data), null, 2);
    } catch {
      return data;
    }
  }
  if (typeof data === 'object') {
    return JSON.stringify(data, null, 2);
  }
  return String(data);
}

const METHOD_CLASS: Record<string, string> = {
  POST: styles.methodPost,
  PUT: styles.methodPut,
  DELETE: styles.methodDelete,
  PATCH: styles.methodPatch,
  GET: styles.methodGet,
};

function DurationCell({ ms }: { ms: number | undefined }) {
  if (ms === undefined || ms === null) return <span>-</span>;
  let cls = styles.durationFast;
  if (ms >= 1000) cls = styles.durationSlow;
  else if (ms >= 200) cls = styles.durationMedium;
  return <span className={cls}>{ms}ms</span>;
}

function statusClass(code: number): string {
  if (code >= 500) return styles.statusErr;
  if (code >= 400) return styles.statusWarn;
  return styles.statusOk;
}

// Convert a preset key into yyyy-mm-dd start/end pair anchored at "now".
function computeRange(preset: string): { start: string; end: string } {
  const end = new Date();
  const start = new Date();
  if (preset === '24h') start.setHours(start.getHours() - 24);
  else if (preset === '7d') start.setDate(start.getDate() - 7);
  else if (preset === '30d') start.setDate(start.getDate() - 30);
  const iso = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  return { start: iso(start), end: iso(end) };
}

export function AuditLogTab() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());

  const urlStart = searchParams.get('start_date') ?? '';
  const urlEnd = searchParams.get('end_date') ?? '';
  const urlTimeRange = searchParams.get('time_range') ?? '';

  // Local state for inputs — debounced text filters apply to URL on change,
  // selects/time picker apply immediately. No "Apply Filters" button — the
  // app's other search bars all behave this way.
  const [username, setUsername] = useState(searchParams.get('username') ?? '');
  const [path, setPath] = useState(searchParams.get('path') ?? '');
  const [statusCodeInput, setStatusCodeInput] = useState(searchParams.get('status_code') ?? '');
  const [method, setMethod] = useState(searchParams.get('method') ?? '');
  const [category, setCategory] = useState(searchParams.get('category') ?? '');
  const [action, setAction] = useState(searchParams.get('action') ?? '');
  const [resourceType, setResourceType] = useState(searchParams.get('resource_type') ?? '');
  const [resourceId, setResourceId] = useState(searchParams.get('resource_id') ?? '');
  const [timeRange, setTimeRange] = useState(
    urlTimeRange || (urlStart && urlEnd ? 'custom' : '24h'),
  );
  const [customRange, setCustomRange] = useState<{ start: string; end: string } | null>(
    urlStart && urlEnd ? { start: urlStart, end: urlEnd } : null,
  );

  // Helper: write a key into searchParams (skip the write if the value is
  // unchanged so we don't churn history).
  const writeParam = (next: URLSearchParams, key: string, value: string | null | undefined) => {
    const v = (value ?? '').trim();
    if (v) next.set(key, v);
    else next.delete(key);
  };

  // Search button (and Enter key) applies the free-text + select filters.
  // Free-text isn't debounced-on-keystroke because the audit table can grow
  // large and every keystroke pause would fire a server-side WHERE+ILIKE.
  function applySearch() {
    const next = new URLSearchParams(searchParams);
    writeParam(next, 'username', username);
    writeParam(next, 'path', path);
    writeParam(next, 'status_code', statusCodeInput);
    writeParam(next, 'method', method);
    writeParam(next, 'category', category);
    writeParam(next, 'action', action);
    writeParam(next, 'resource_type', resourceType);
    writeParam(next, 'resource_id', resourceId);
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
      setPage(1);
    }
  }

  function handleEnter(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault();
      applySearch();
    }
  }

  // Time range applies immediately on preset click / custom apply.
  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    let start = '';
    let end = '';
    if (timeRange === 'custom' && customRange) {
      start = customRange.start;
      end = customRange.end;
      next.set('time_range', 'custom');
    } else if (['24h', '7d', '30d'].includes(timeRange)) {
      const r = computeRange(timeRange);
      start = r.start;
      end = r.end;
      next.set('time_range', timeRange);
    } else {
      next.delete('time_range');
    }
    writeParam(next, 'start_date', start);
    writeParam(next, 'end_date', end);
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
      setPage(1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeRange, customRange]);

  const appliedParams = {
    page,
    per_page: 50,
    start_date: urlStart || undefined,
    end_date: urlEnd || undefined,
    username: searchParams.get('username') || undefined,
    method: searchParams.get('method') || undefined,
    path: searchParams.get('path') || undefined,
    status_code: searchParams.get('status_code') || undefined,
    category: searchParams.get('category') || undefined,
    action: searchParams.get('action') || undefined,
    resource_type: searchParams.get('resource_type') || undefined,
    resource_id: searchParams.get('resource_id') || undefined,
  };

  const { data, isLoading } = useAuditLog(appliedParams);

  function toggleRow(id: string) {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const entries: AuditLogEntry[] = data?.entries ?? [];
  const totalPages = data?.pages ?? 1;
  const total = data?.total ?? 0;

  return (
    <div>
      {/* Filter bar */}
      <div className={styles.filterBar}>
        <div className={styles.filterField}>
          <label className={styles.filterLabel}>Username</label>
          <input
            type="text"
            className={styles.filterInput}
            placeholder="e.g. admin"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={handleEnter}
          />
        </div>
        <div className={styles.filterField}>
          <label className={styles.filterLabel}>Method</label>
          <select
            className={styles.filterSelect}
            value={method}
            onChange={(e) => setMethod(e.target.value)}
          >
            <option value="">All</option>
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="DELETE">DELETE</option>
            <option value="PATCH">PATCH</option>
            <option value="GET">GET</option>
          </select>
        </div>
        <div className={styles.filterField}>
          <label className={styles.filterLabel}>Path</label>
          <input
            type="text"
            className={styles.filterInput}
            placeholder="e.g. /api/assets"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={handleEnter}
          />
        </div>
        <div className={styles.filterField}>
          <label className={styles.filterLabel}>Status Code</label>
          <input
            type="text"
            className={styles.filterInput}
            placeholder="e.g. 200"
            style={{ minWidth: '80px' }}
            value={statusCodeInput}
            onChange={(e) => setStatusCodeInput(e.target.value)}
            onKeyDown={handleEnter}
          />
        </div>
        <div className={styles.filterField}>
          <label className={styles.filterLabel}>Category</label>
          <input
            type="text"
            className={styles.filterInput}
            placeholder="e.g. config"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            onKeyDown={handleEnter}
          />
        </div>
        <div className={styles.filterField}>
          <label className={styles.filterLabel}>Action</label>
          <input
            type="text"
            className={styles.filterInput}
            placeholder="e.g. update"
            value={action}
            onChange={(e) => setAction(e.target.value)}
            onKeyDown={handleEnter}
          />
        </div>
        <div className={styles.filterField}>
          <label className={styles.filterLabel}>Resource Type</label>
          <input
            type="text"
            className={styles.filterInput}
            placeholder="e.g. integration"
            value={resourceType}
            onChange={(e) => setResourceType(e.target.value)}
            onKeyDown={handleEnter}
          />
        </div>
        <div className={styles.filterField}>
          <label className={styles.filterLabel}>Resource ID</label>
          <input
            type="text"
            className={styles.filterInput}
            placeholder="e.g. sentinelone"
            value={resourceId}
            onChange={(e) => setResourceId(e.target.value)}
            onKeyDown={handleEnter}
          />
        </div>
        {/* Search button — mirrors the input height so it sits flush with
            the row of filters. Spacer label keeps it bottom-aligned with
            the labelled inputs. */}
        <div className={styles.filterField}>
          <label className={styles.filterLabel} aria-hidden>&nbsp;</label>
          <Button size="sm" className={styles.searchButton} onClick={applySearch}>
            Search
          </Button>
        </div>
        {/* Time picker docks to the right — same convention as the alerts
            page top bar. Applies immediately (cheap, indexed). */}
        <div className={`${styles.filterField} ${styles.filterFieldRight}`}>
          <label className={styles.filterLabel} aria-hidden>&nbsp;</label>
          <TimeRangeGroup
            value={timeRange}
            onChange={(v) => {
              setTimeRange(v);
              if (v !== 'custom') setCustomRange(null);
            }}
            customRange={customRange}
            onCustomRange={(start, end) => setCustomRange({ start, end })}
          />
        </div>
      </div>

      {/* Table card */}
      <div className={styles.tableCard}>
        {isLoading ? (
          <LoadingSpinner />
        ) : (
          <table className={styles.table}>
            <thead>
              <tr className={styles.tableHeader}>
                <th>Timestamp</th>
                <th>User</th>
                <th>Method</th>
                <th>Path</th>
                <th>Status</th>
                <th>Category</th>
                <th>Action</th>
                <th>Resource</th>
                <th>IP</th>
                <th>Duration</th>
              </tr>
            </thead>
            {entries.length === 0 ? (
              <tbody>
                <tr>
                  <td colSpan={10}>
                    <div className={styles.empty}>
                      <p>No audit entries found</p>
                    </div>
                  </td>
                </tr>
              </tbody>
            ) : (
              <tbody>
                {entries.map((entry, idx) => {
                  const id = entry.id ?? String(idx);
                  const isExpanded = expandedRows.has(id);
                  const pathVal = entry.path ?? entry.endpoint ?? '-';
                  const user = entry.username ?? entry.user_id ?? '-';
                  const ip = entry.ip_address ?? entry.client_ip ?? '-';
                  const statusCode = entry.status_code ?? entry.response_status;
                  const ts = entry.timestamp ?? entry.created_at;
                  const m = (entry.method ?? '').toUpperCase();
                  // NULL semantic fields = an HTTP-only row (nothing annotated
                  // the request) — render an em-dash, not a blank cell.
                  const categoryVal = entry.category ?? '—';
                  const actionVal = entry.action ?? '—';
                  const resourceVal = entry.resource_type
                    ? (entry.resource_id
                      ? `${entry.resource_type}:${entry.resource_id}`
                      : entry.resource_type)
                    : '—';

                  return (
                    <React.Fragment key={id}>
                      <tr
                        className={styles.row}
                        onClick={() => toggleRow(id)}
                      >
                        <td>{formatTimestamp(ts)}</td>
                        <td>{user}</td>
                        <td>
                          <span className={`${styles.methodText} ${METHOD_CLASS[m] ?? ''}`}>
                            {m}
                          </span>
                        </td>
                        <td>
                          <span className={styles.pathCell} title={pathVal}>
                            {pathVal}
                          </span>
                        </td>
                        <td>
                          {statusCode == null ? (
                            <span>-</span>
                          ) : (
                            <span className={`${styles.statusText} ${statusClass(statusCode)}`}>
                              {statusCode}
                            </span>
                          )}
                        </td>
                        <td>{categoryVal}</td>
                        <td>{actionVal}</td>
                        <td>
                          <span className={styles.pathCell} title={resourceVal !== '—' ? resourceVal : undefined}>
                            {resourceVal}
                          </span>
                        </td>
                        <td>{ip}</td>
                        <td><DurationCell ms={entry.duration_ms} /></td>
                      </tr>
                      <tr
                        className={`${styles.detailRow}${isExpanded ? ` ${styles.expanded}` : ''}`}
                      >
                        <td colSpan={10}>
                          <div className={styles.detailContent}>
                            <div className={styles.detailSection}>
                              <h4>Request Body</h4>
                              <pre>{formatJson(entry.request_body)}</pre>
                            </div>
                            <div className={styles.detailSection}>
                              <h4>Response Summary</h4>
                              <pre>{formatJson(entry.response_summary)}</pre>
                            </div>
                            {entry.details != null && (
                              <div className={styles.detailSection}>
                                <h4>Details</h4>
                                <pre>{formatJson(entry.details)}</pre>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    </React.Fragment>
                  );
                })}
              </tbody>
            )}
          </table>
        )}

        {/* Pagination */}
        {!isLoading && entries.length > 0 && (
          <div className={styles.pagination}>
            <Button
              size="sm"
              variant="secondary"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              Previous
            </Button>
            <span className={styles.pageInfo}>
              Page {page} of {totalPages} ({total} entries)
            </span>
            <Button
              size="sm"
              variant="secondary"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
