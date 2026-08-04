// SPDX-License-Identifier: AGPL-3.0-or-later
import { Fragment, useState, useMemo } from 'react';
import type { HistoryEvent } from '@/api/asset-history';
import { DataSourceBadge } from '@/components/ui/DataSourceBadge';
import styles from './asset-history.module.css';

interface HistoryTableProps {
  events: HistoryEvent[];
  drillDate?: string | null;
  onClearDrill?: () => void;
}

type SortColumn = 'timestamp' | 'type' | 'attribute' | 'source';
type SortDir = 'asc' | 'desc';

const PAGE_SIZE = 20;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(ts: string): string {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function truncate(value: string | null | undefined, max: number): string {
  if (!value) return '-';
  return value.length > max ? value.slice(0, max) + '...' : value;
}

const TRUNCATE_LIMIT = 40;

function ExpandableCell({
  id,
  value,
  isExpanded,
  onToggle,
}: {
  id: string;
  value: string | null | undefined;
  isExpanded: boolean;
  onToggle: (id: string) => void;
}) {
  const text = value ?? '';
  const isLong = text.length > TRUNCATE_LIMIT;
  if (!text) return <td className={styles.td}>-</td>;
  if (!isLong) return <td className={styles.td}>{text}</td>;

  return (
    <td
      className={`${styles.td} ${styles.expandableCell}`}
      onClick={() => onToggle(id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onToggle(id);
        }
      }}
    >
      <span className={styles.expandableCellText}>{truncate(text, TRUNCATE_LIMIT)}</span>
      <svg
        className={`${styles.expandableCellChevron} ${isExpanded ? styles.expandableCellChevronOpen : ''}`}
        viewBox="0 0 24 24"
        width="16"
        height="16"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <polyline points="6 9 12 15 18 9" />
      </svg>
    </td>
  );
}

const ACTION_COLORS: Record<string, string> = {
  added: '#10b981',
  updated: '#3b82f6',
  removed: '#f59e0b',
};

const ALERT_COLOR = '#ef4444';

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function HistoryTable({ events, drillDate, onClearDrill }: HistoryTableProps) {
  const [search, setSearch] = useState('');
  const [eventTypeFilter, setEventTypeFilter] = useState<string>('');
  const [sortCol, setSortCol] = useState<SortColumn>('timestamp');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [page, setPage] = useState(1);
  const [expandedCell, setExpandedCell] = useState<string | null>(null);

  // Filter
  const filtered = useMemo(() => {
    let result = events;

    if (eventTypeFilter) {
      // Action filters (added/updated/removed) match e.action;
      // 'alert' matches by event type since alerts don't carry an action.
      if (eventTypeFilter === 'alert') {
        result = result.filter((e) => e.type === 'alert');
      } else {
        result = result.filter((e) => e.action === eventTypeFilter);
      }
    }

    if (search) {
      const q = search.toLowerCase();
      result = result.filter((e) =>
        (e.attribute || '').toLowerCase().includes(q) ||
        (e.old_value || '').toLowerCase().includes(q) ||
        (e.new_value || '').toLowerCase().includes(q) ||
        (e.source || '').toLowerCase().includes(q) ||
        (e.title || '').toLowerCase().includes(q) ||
        (e.action || '').toLowerCase().includes(q)
      );
    }

    return result;
  }, [events, eventTypeFilter, search]);

  // Sort
  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      let cmp = 0;
      switch (sortCol) {
        case 'timestamp':
          cmp = (a.timestamp || '').localeCompare(b.timestamp || '');
          break;
        case 'type':
          cmp = (a.type || '').localeCompare(b.type || '');
          break;
        case 'attribute':
          cmp = (a.attribute || '').localeCompare(b.attribute || '');
          break;
        case 'source':
          cmp = (a.source || '').localeCompare(b.source || '');
          break;
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return arr;
  }, [filtered, sortCol, sortDir]);

  // Paginate
  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const paginated = sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  function handleSort(col: SortColumn) {
    if (sortCol === col) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortCol(col);
      setSortDir('desc');
    }
    setPage(1);
  }

  function handleSearch(value: string) {
    setSearch(value);
    setPage(1);
  }

  function handleFilterChange(value: string) {
    setEventTypeFilter(value);
    setPage(1);
  }

  const sortIndicator = (col: SortColumn) =>
    sortCol === col ? (sortDir === 'asc' ? ' \u25B2' : ' \u25BC') : '';

  return (
    <div>
      {/* Toolbar */}
      <div className={styles.tableToolbar}>
        <div className={styles.tableToolbarLeft}>
          <div className={styles.tableSearch}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14">
              <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              type="text"
              placeholder="Search events..."
              value={search}
              onChange={(e) => handleSearch(e.target.value)}
              className={styles.tableSearchInput}
            />
          </div>
          <select
            value={eventTypeFilter}
            onChange={(e) => handleFilterChange(e.target.value)}
            className={styles.eventTypeSelect}
          >
            <option value="">All Events</option>
            <option value="added">Added</option>
            <option value="updated">Updated</option>
            <option value="removed">Removed</option>
          </select>
          {drillDate && (
            <span className={styles.drillPill}>
              {drillDate}
              <button
                className={styles.drillClose}
                onClick={onClearDrill}
                aria-label="Clear chart selection"
                type="button"
              >
                ×
              </button>
            </span>
          )}
        </div>
        <span className={styles.resultCount}>{sorted.length} events</span>
      </div>

      {/* Table */}
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.th} style={{ width: 100 }} onClick={() => handleSort('timestamp')}>
                Date{sortIndicator('timestamp')}
              </th>
              <th className={styles.th} style={{ width: 100 }} onClick={() => handleSort('type')}>
                Event{sortIndicator('type')}
              </th>
              <th className={styles.th} style={{ width: 200 }} onClick={() => handleSort('attribute')}>
                Attribute{sortIndicator('attribute')}
              </th>
              <th className={styles.th} style={{ width: '25%' }}>Old Value</th>
              <th className={styles.th} style={{ width: '25%' }}>New Value</th>
              <th className={styles.th} style={{ width: 180 }} onClick={() => handleSort('source')}>
                Source{sortIndicator('source')}
              </th>
            </tr>
          </thead>
          <tbody>
            {paginated.length === 0 && (
              <tr>
                <td colSpan={6} className={styles.emptyState}>No events found</td>
              </tr>
            )}
            {paginated.map((event) => {
              const oldId = `${event.id}-old`;
              const newId = `${event.id}-new`;
              const oldOpen = expandedCell === oldId;
              const newOpen = expandedCell === newId;
              const expandedText = oldOpen ? event.old_value : newOpen ? event.new_value : null;
              const expandedLabel = oldOpen ? 'Old Value' : newOpen ? 'New Value' : '';
              const toggle = (id: string) =>
                setExpandedCell((prev) => (prev === id ? null : id));
              return (
                <Fragment key={event.id}>
                  <tr className={styles.tr}>
                    <td className={styles.td}>{formatDate(event.timestamp)}</td>
                    <td className={styles.td}>
                      {event.type === 'attribute' && event.action ? (
                        <span className={styles.eventType} style={{ color: ACTION_COLORS[event.action] || '#3b82f6' }}>
                          {event.action}
                        </span>
                      ) : event.type === 'alert' ? (
                        <span className={styles.eventType} style={{ color: ALERT_COLOR }}>
                          {event.alert_type || 'alert'}
                        </span>
                      ) : (
                        <span>-</span>
                      )}
                    </td>
                    <td className={styles.td}>
                      {event.type === 'attribute' ? (event.attribute || '-') : (event.title || '-')}
                    </td>
                    <ExpandableCell
                      id={oldId}
                      value={event.old_value}
                      isExpanded={oldOpen}
                      onToggle={toggle}
                    />
                    <ExpandableCell
                      id={newId}
                      value={event.new_value}
                      isExpanded={newOpen}
                      onToggle={toggle}
                    />
                    <td className={styles.td}>
                      {event.source ? <DataSourceBadge name={event.source} /> : '-'}
                    </td>
                  </tr>
                  {expandedText && (
                    <tr className={styles.expandRow}>
                      <td colSpan={6}>
                        <div className={styles.expandRowContent}>
                          <span className={styles.expandRowLabel}>{expandedLabel}</span>
                          <pre className={styles.expandRowText}>{expandedText}</pre>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className={styles.pagination}>
          <button
            className={styles.pageBtn}
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            &lt;
          </button>
          {Array.from({ length: totalPages }, (_, i) => i + 1)
            .filter((p) => p === 1 || p === totalPages || Math.abs(p - page) <= 2)
            .map((p, idx, arr) => (
              <span key={p}>
                {idx > 0 && arr[idx - 1] !== p - 1 && (
                  <span className={styles.pageEllipsis}>...</span>
                )}
                <button
                  className={`${styles.pageBtn} ${p === page ? styles.pageBtnActive : ''}`}
                  onClick={() => setPage(p)}
                >
                  {p}
                </button>
              </span>
            ))}
          <button
            className={styles.pageBtn}
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            &gt;
          </button>
        </div>
      )}
    </div>
  );
}
