// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useMemo } from 'react';
import type { AssetProvider, OtherSource } from '@/api/asset-details';
import { DataSourceBadge } from '@/components/ui/DataSourceBadge';
import { formatDate } from '../explorer/explorer-utils';
import styles from './asset-details.module.css';

interface DataSourcesProps {
  providers: AssetProvider[];
  otherSources: OtherSource[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const EMPTY_STRINGS = new Set(['', 'NaT', 'NaN', 'None']);

function isEmptyValue(value: unknown): boolean {
  return value === null || value === undefined || EMPTY_STRINGS.has(String(value));
}

function formatFieldValue(value: unknown): string {
  if (isEmptyValue(value)) return '-';
  const str = String(value);
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(str)) {
    return formatDate(str);
  }
  return str;
}

function formatFieldName(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// ---------------------------------------------------------------------------
// Source detail panel
// ---------------------------------------------------------------------------

function FieldSection({
  title,
  fields,
  twoColumn = false,
}: {
  title: string;
  fields: Record<string, unknown>;
  twoColumn?: boolean;
}) {
  const entries = Object.entries(fields).filter(([, v]) => !isEmptyValue(v));

  if (entries.length === 0) return null;

  return (
    <div className={styles.fieldSection}>
      <h4 className={styles.fieldSectionTitle}>{title}</h4>
      <div className={twoColumn ? styles.fieldRowsTwoCol : undefined}>
        {entries.map(([key, val]) => (
          <div key={key} className={styles.sourceFieldRow}>
            <span className={styles.sourceFieldLabel}>{formatFieldName(key)}</span>
            <span className={styles.sourceFieldValue}>{formatFieldValue(val)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function filterFields(fields: Record<string, unknown>, query: string): Record<string, unknown> {
  if (!query) return fields;
  const q = query.toLowerCase();
  return Object.fromEntries(
    Object.entries(fields).filter(([k, v]) =>
      k.toLowerCase().includes(q) || String(v).toLowerCase().includes(q),
    ),
  );
}

function hasFields(fields: Record<string, unknown>): boolean {
  return Object.keys(fields).length > 0;
}

function SourceDetailPanel({ provider, search }: { provider: AssetProvider; search: string }) {
  const general = filterFields(provider.fields.general, search);
  const network = filterFields(provider.fields.network, search);
  const extraFields = {
    ...filterFields(provider.fields.additional, search),
    ...filterFields(provider.fields.extended, search),
  };

  return (
    <div className={styles.sourceDetail}>
      <div className={styles.sourceDetailHeader}>
        <DataSourceBadge name={provider.source_name || provider.source_type} className={styles.sourceBadgeFullName} />
        <span className={styles.sourceFieldCount}>{provider.field_count} fields</span>
        {provider.last_sync && (
          <span className={styles.sourceSync}>Synced {formatDate(provider.last_sync)}</span>
        )}
      </div>
      {(hasFields(general) || hasFields(network)) && (
        <div className={styles.sourceFieldsGrid}>
          {hasFields(general) && <FieldSection title="General" fields={general} />}
          {hasFields(network) && <FieldSection title="Network" fields={network} />}
        </div>
      )}
      {hasFields(extraFields) && <FieldSection title="Extended" fields={extraFields} twoColumn />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

export function DataSources({ providers, otherSources }: DataSourcesProps) {
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [search, setSearch] = useState('');

  const activeProviders = useMemo(
    () => providers.filter((p) => p.status === 'active'),
    [providers],
  );

  const selected = activeProviders[selectedIdx] ?? activeProviders[0];

  return (
    <div className={styles.sourcesSection}>
      <div className={styles.sourcesTopRow}>
        <div className={styles.sourcesSectionHeader}>
          <h3 className={styles.sectionTitle}>Data Sources</h3>
          <span className={styles.sourcesCount}>{activeProviders.length}</span>
        </div>

        <div className={styles.sourcesSearch}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14">
            <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="text"
            placeholder="Search fields..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className={styles.sourcesSearchInput}
          />
        </div>
      </div>

      <div className={styles.sourcesLayout}>
        {/* Source list */}
        <div className={styles.sourceList}>
          {activeProviders.map((p, i) => (
            <div
              key={p.source_id}
              className={`${styles.sourceItem} ${i === selectedIdx ? styles.sourceItemActive : ''}`}
              onClick={() => setSelectedIdx(i)}
            >
              <DataSourceBadge name={p.source_name || p.source_type} className={styles.sourceBadgeFullName} />
              <div className={styles.sourceItemMeta}>
                <span className={styles.sourceItemCount}>{p.field_count} fields</span>
              </div>
            </div>
          ))}

          {otherSources.length > 0 && (
            <>
              <div className={styles.sourceDivider}>Other Sources</div>
              {otherSources.map((s) => (
                <div key={s.source_name} className={`${styles.sourceItem} ${styles.sourceItemInactive}`}>
                  <DataSourceBadge name={s.source_name} className={styles.sourceBadgeFullName} />
                  <span className={styles.sourceItemCount}>{s.asset_count} assets</span>
                </div>
              ))}
            </>
          )}
        </div>

        {/* Detail panel */}
        {selected && <SourceDetailPanel provider={selected} search={search} />}
      </div>
    </div>
  );
}
