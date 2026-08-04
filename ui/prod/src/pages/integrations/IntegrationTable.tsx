// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { DataSourceBadge } from '@/components/ui/DataSourceBadge';
import { DeleteIconButton, IconButton } from '@/components/ui/IconButton';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import { resolveLogoUrl } from '@/lib/logoUrl';
import type { Integration } from '@/api/integrations';
import { formatRelativeTime, formatFutureTime, getConnectorDisplayName, getScheduleDescription, getSourceMethodLabel } from './integration-utils';
import styles from './IntegrationTable.module.css';

interface IntegrationTableProps {
  integrations: Integration[];
  onRun: (id: string) => void;
  onToggle: (id: string, enable: boolean) => void;
  onEdit: (id: string) => void;
  onClone: (id: string) => void;
  onDelete: (id: string) => void;
}

type SortColumn = 'name' | 'type' | 'status' | 'schedule' | 'lastSync' | 'nextRun' | 'records' | 'created' | 'enabled';
type SortDirection = 'asc' | 'desc';

interface SortConfig {
  column: SortColumn;
  direction: SortDirection;
}

const COLUMNS: { key: SortColumn; label: string }[] = [
  { key: 'name', label: 'Name' },
  { key: 'type', label: 'Connector' },
  { key: 'status', label: 'Status' },
  { key: 'schedule', label: 'Schedule' },
  { key: 'lastSync', label: 'Last sync' },
  { key: 'nextRun', label: 'Next Run' },
  { key: 'records', label: 'Records' },
  { key: 'created', label: 'Created' },
];

function getSortValue(integration: Integration, column: SortColumn): string | number {
  switch (column) {
    case 'name':
      return integration.name.toLowerCase();
    case 'type':
      return (
        getSourceMethodLabel(integration.source_method) ??
        getConnectorDisplayName(integration.integration_type)
      ).toLowerCase();
    case 'status':
      return integration.status;
    case 'schedule':
      return integration.schedule_cron ?? '';
    case 'lastSync':
      return integration.last_sync_at ?? '';
    case 'nextRun':
      return integration.next_run_at ?? '';
    case 'records':
      return integration.latest_batch_records ?? 0;
    case 'created':
      return integration.created_at;
    case 'enabled':
      return integration.status === 'enabled' ? 1 : 0;
  }
}

export function IntegrationTable({
  integrations,
  onRun,
  onToggle,
  onEdit,
  onClone,
  onDelete,
}: IntegrationTableProps) {
  const [sort, setSort] = useState<SortConfig>({ column: 'name', direction: 'asc' });

  function handleHeaderClick(column: SortColumn) {
    setSort((prev) =>
      prev.column === column
        ? { column, direction: prev.direction === 'asc' ? 'desc' : 'asc' }
        : { column, direction: 'asc' },
    );
  }

  const sorted = useMemo(() => {
    return [...integrations].sort((a, b) => {
      const aVal = getSortValue(a, sort.column);
      const bVal = getSortValue(b, sort.column);
      if (aVal < bVal) return sort.direction === 'asc' ? -1 : 1;
      if (aVal > bVal) return sort.direction === 'asc' ? 1 : -1;
      return 0;
    });
  }, [integrations, sort]);

  const sortIndicator = (
    <span className={styles.sortIcon}>
      {sort.direction === 'asc' ? (
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 15l-6-6-6 6"/></svg>
      ) : (
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6"/></svg>
      )}
    </span>
  );

  return (
    <div className={styles.tableWrapper}>
      <table className={styles.table}>
        <colgroup><col style={{ width: '18%' }} /><col style={{ width: '16%' }} /><col style={{ width: '7%' }} /><col style={{ width: '12%' }} /><col style={{ width: '10%' }} /><col style={{ width: '10%' }} /><col style={{ width: '7%' }} /><col style={{ width: '7%' }} /><col style={{ width: '10%' }} /><col style={{ width: '50px' }} /></colgroup>
        <thead>
          <tr>
            {COLUMNS.map(({ key, label }) => {
              const isActive = sort.column === key;
              return (
                <th
                  key={key}
                  className={[styles.th, styles.thSortable, isActive ? styles.thActive : ''].filter(Boolean).join(' ')}
                  onClick={() => handleHeaderClick(key)}
                >
                  {label}
                  {isActive && sortIndicator}
                </th>
              );
            })}
            <th className={styles.th}>Actions</th>
            <th
              className={[styles.thToggle, styles.thSortable, sort.column === 'enabled' ? styles.thActive : ''].filter(Boolean).join(' ')}
              onClick={() => handleHeaderClick('enabled')}
            >
              {sort.column === 'enabled' && sortIndicator}
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((integration) => {
            const isEnabled = integration.status === 'enabled';
            return (
              <tr key={integration.id} className={styles.tr}>
                <td className={styles.td}>
                  <div className={styles.nameCell}>
                    {integration.logo && (
                      <img src={resolveLogoUrl(integration.logo) ?? ''} alt="" className={styles.logo} />
                    )}
                    <Link to={`/integration-details?id=${integration.id}`} className={styles.nameLink}>
                      {integration.name}
                    </Link>
                  </div>
                </td>
                <td className={styles.td}>
                  <DataSourceBadge
                    name={
                      getSourceMethodLabel(integration.source_method) ??
                      getConnectorDisplayName(integration.integration_type)
                    }
                    tinted
                  />
                </td>
                <td className={styles.td}>
                  {integration.is_running ? (
                    <span className={`${styles.statusText} ${styles.statusInfo}`}>Running</span>
                  ) : integration.status === 'error' ? (
                    <span className={`${styles.statusText} ${styles.statusError}`}>Error</span>
                  ) : (
                    <span className={`${styles.statusText} ${isEnabled ? styles.statusSuccess : styles.statusMuted}`}>
                      {isEnabled ? 'Enabled' : 'Disabled'}
                    </span>
                  )}
                </td>
                <td className={styles.td}>{integration.schedule_enabled ? getScheduleDescription(integration.schedule_cron) : 'Not scheduled'}</td>
                <td className={styles.td}>{formatRelativeTime(integration.last_sync_at)}</td>
                <td className={styles.td}>{integration.schedule_enabled ? (formatFutureTime(integration.next_run_at) ?? 'Not scheduled') : 'Not scheduled'}</td>
                <td className={styles.td}>{integration.is_running && integration.latest_batch_records === 0 ? '—' : integration.latest_batch_records?.toLocaleString() ?? '—'}</td>
                <td className={styles.td}>{formatRelativeTime(integration.created_at)}</td>
                <td className={styles.td}>
                  <div className={styles.actionsCell}>
                    <IconButton onClick={() => !integration.is_running && onRun(integration.id)} title="Run Now" disabled={integration.is_running}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                    </IconButton>
                    <IconButton onClick={() => onEdit(integration.id)} title="Edit">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                    </IconButton>
                    <IconButton onClick={() => onClone(integration.id)} title="Clone">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                    </IconButton>
                    <DeleteIconButton onClick={() => onDelete(integration.id)} title="Delete" />
                  </div>
                </td>
                <td className={styles.tdToggle}>
                  <ToggleSwitch
                    checked={isEnabled}
                    onChange={(checked) => onToggle(integration.id, checked)}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
