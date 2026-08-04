// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useMemo } from 'react';
import { Button } from '@/components/ui/Button';
import { useMappingPreview } from '@/api/integrations';
import { typeBadgeStyle, getCoverageColor } from '@/pages/integrations/integration-utils';
import styles from './MappingPreview.module.css';

interface MappingPreviewProps {
  integrationId: string;
  refetchInterval: number;
}

const PAGE_SIZE = 10;

const FRAMEWORK_FIELDS = new Set([
  'hostname', 'fqdn', 'ip', 'mac', 'sn',
  'os', 'manufacturer', 'model', 'vendor_category',
  'first_time', 'last_time', 'user',
]);

function formatValue(value: any): string {
  if (value == null) return '';
  if (typeof value === 'object') {
    const json = JSON.stringify(value);
    return json.length > 120 ? json.slice(0, 120) + '...' : json;
  }
  const str = String(value);
  return str.length > 120 ? str.slice(0, 120) + '...' : str;
}

function getRecordPreview(mapped: Record<string, any>, columns: string[]): { field: string; value: string }[] {
  const parts: { field: string; value: string }[] = [];
  for (const col of columns) {
    const val = mapped?.[col];
    if (val != null && val !== '') {
      const str = String(val);
      parts.push({ field: col, value: str.length > 30 ? str.slice(0, 27) + '...' : str });
    }
    if (parts.length >= 4) break;
  }
  return parts;
}

export function MappingPreview({ integrationId, refetchInterval }: MappingPreviewProps) {
  const [viewMode, setViewMode] = useState<'records' | 'fields'>('records');
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [offset, setOffset] = useState(0);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [expandedFields, setExpandedFields] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'ok' | 'dropped'>('all');

  const { data, isLoading } = useMappingPreview(
    integrationId,
    { limit: pageSize, offset, status: statusFilter },
    { refetchInterval },
  );

  const records = data?.records ?? [];
  const total = data?.total_records ?? 0;
  const filteredTotal = data?.filtered_total ?? total;
  const droppedCount = data?.dropped_count ?? 0;
  const columns: string[] = data?.columns ?? [];
  const sourceMap: Record<string, string> = data?.source_map ?? {};
  const transformMap: Record<string, string[]> = data?.transform_map ?? {};
  const fieldStats: Record<string, any>[] = data?.field_stats ?? [];
  const droppedPct = total > 0 ? ((droppedCount / total) * 100).toFixed(1) : '0';

  // Status (all/ok/dropped) is filtered server-side so 'Dropped' finds drops
  // across the whole batch, not just the loaded page. Text search refines the
  // records already on the page.
  const filteredRecords = useMemo(() => {
    if (!search) return records;
    const q = search.toLowerCase();
    return records.filter((rec) => {
      for (const obj of [rec.mapped, rec.raw, rec.virtual_fields]) {
        if (!obj) continue;
        for (const [k, v] of Object.entries(obj)) {
          if (k.toLowerCase().includes(q)) return true;
          if (v != null && String(v).toLowerCase().includes(q)) return true;
        }
      }
      return false;
    });
  }, [records, search]);

  const filteredFieldStats = useMemo(() => {
    if (!search) return fieldStats;
    const q = search.toLowerCase();
    return fieldStats.filter((f: any) =>
      f.path?.toLowerCase().includes(q) ||
      f.type?.toLowerCase().includes(q) ||
      f.mapped_to?.toLowerCase().includes(q),
    );
  }, [fieldStats, search]);

  // Pagination follows the server-side status filter (filtered_total). A text
  // search only refines the loaded page, so use its local count in that case.
  const recordsTotal = search ? filteredRecords.length : filteredTotal;
  const totalItems = viewMode === 'fields' ? filteredFieldStats.length : recordsTotal;
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const currentPage = Math.floor(offset / pageSize) + 1;
  const pagedFieldStats = filteredFieldStats.slice(offset, offset + pageSize);

  // Unmapped framework fields
  const unmappedFramework = useMemo(() => {
    const mappedTargets = new Set(columns);
    return [...FRAMEWORK_FIELDS].filter(f => !mappedTargets.has(f));
  }, [columns]);

  // For transpose: which raw fields feed transforms
  const feedsTransform = useMemo(() => {
    const set = new Set<string>();
    for (const srcFields of Object.values(transformMap)) {
      for (const f of srcFields) set.add(f);
    }
    return set;
  }, [transformMap]);

  function toggleRecord(rowNum: number) {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(rowNum)) next.delete(rowNum);
      else next.add(rowNum);
      return next;
    });
  }

  function toggleField(path: string) {
    setExpandedFields(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>Mapping Preview</div>
      {/* Header bar */}
      <div className={styles.headerBar}>
        <div className={styles.viewToggle}>
          <button
            className={`${styles.toggleButton} ${viewMode === 'records' ? styles.toggleButtonActive : ''}`}
            onClick={() => { setViewMode('records'); setOffset(0); }}
          >
            Records
          </button>
          <button
            className={`${styles.toggleButton} ${viewMode === 'fields' ? styles.toggleButtonActive : ''}`}
            onClick={() => { setViewMode('fields'); setOffset(0); }}
          >
            Fields
          </button>
        </div>
        <input
          className={styles.searchInput}
          type="text"
          placeholder="Search..."
          value={search}
          onChange={(e) => { setSearch(e.target.value); setOffset(0); }}
        />
        {viewMode === 'records' && (
          <label className={styles.statusFilter}>
            <span className={styles.statusFilterLabel}>Status:</span>
            <select
              className={styles.statusSelect}
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value as 'all' | 'ok' | 'dropped');
                setOffset(0);
              }}
              aria-label="Mapping status"
            >
              <option value="all">All</option>
              <option value="ok">OK</option>
              <option value="dropped">Dropped</option>
            </select>
          </label>
        )}
        <span className={styles.statText}>{total.toLocaleString()} records</span>
        {droppedCount > 0 ? (
          <span className={styles.statTextDanger}>{droppedCount.toLocaleString()} dropped ({droppedPct}%)</span>
        ) : (
          <span className={styles.statText}>0 dropped</span>
        )}
      </div>

      {isLoading ? (
        <div className={styles.emptyState}>Loading...</div>
      ) : records.length === 0 && fieldStats.length === 0 ? (
        <div className={styles.emptyState}>No records to preview. Run a data collection first.</div>
      ) : viewMode === 'records' ? (
        /* ─── Records View ─── */
        <div className={styles.list}>
          {filteredRecords.map((rec) => {
            const isDropped = rec.dropped === true;
            const isExpanded = expandedIds.has(rec.row_num);
            const mapped = rec.mapped || {};
            const raw = rec.raw || {};
            const virtualFields = rec.virtual_fields || {};
            const preview = getRecordPreview(mapped, columns);
            const rawFields = Object.keys(raw);
            const tmEntries = Object.entries(transformMap);

            const directMapped = rawFields.filter(f => sourceMap[f] && !feedsTransform.has(f));
            const unmapped = rawFields.filter(f => !sourceMap[f] && !feedsTransform.has(f));

            return (
              <div
                key={rec.row_num}
                className={`${styles.recordCard} ${isDropped ? styles.recordDropped : ''} ${isExpanded ? styles.recordExpanded : ''}`}
              >
                <div className={styles.recordHeader} onClick={() => toggleRecord(rec.row_num)}>
                  <div className={styles.recordPreview}>
                    {preview.length > 0 ? preview.map((p, i) => (
                      <span key={i}>
                        {i > 0 && <span className={styles.previewSep}> | </span>}
                        <span className={styles.previewField}>{p.field}:</span> {p.value}
                      </span>
                    )) : (
                      <span className={styles.textMuted}>No mapped values</span>
                    )}
                  </div>
                  <span className={isDropped ? styles.statTextDanger : styles.statTextOk}>
                    {isDropped ? 'Dropped' : 'OK'}
                  </span>
                  <svg className={`${styles.chevron} ${isExpanded ? styles.chevronOpen : ''}`} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="6 9 12 15 18 9"/></svg>
                </div>

                {isExpanded && (
                  <div className={styles.recordBody}>
                    {isDropped && rec.drop_reasons && (
                      <div className={styles.dropReason}>
                        {(Array.isArray(rec.drop_reasons) ? rec.drop_reasons : [rec.drop_reasons]).join(', ')}
                      </div>
                    )}
                    <table className={styles.transposeTable}>
                      {/* Give the source-field column a generous slice so
                          common names + transform subtitles fit without
                          wrapping; the value column keeps the rest. */}
                      <colgroup>
                        <col className={styles.colSourceField} />
                        <col />
                        <col className={styles.colMapsTo} />
                      </colgroup>
                      <thead>
                        <tr>
                          <th className={styles.transposeTh}>Source Field</th>
                          <th className={styles.transposeTh}>Value</th>
                          <th className={styles.transposeTh}>Maps To</th>
                        </tr>
                      </thead>
                      <tbody>
                        {/* Directly mapped fields */}
                        {directMapped.map(field => (
                          <tr key={field} className={styles.rowMapped}>
                            <td className={styles.transposeTd}><span>{field}</span></td>
                            <td className={styles.transposeTdValue}>{formatValue(raw[field]) || <em className={styles.textMuted}>null</em>}</td>
                            <td className={styles.transposeTd}>
                              <span className={styles.mapsToFramework}>→ {sourceMap[field]}</span>
                            </td>
                          </tr>
                        ))}

                        {/* Virtual/transformed fields */}
                        {tmEntries.map(([vfName, srcFields]) => (
                          <tr key={vfName} className={styles.rowMapped}>
                            <td className={styles.transposeTd}>
                              <span>{vfName}</span>
                              <div className={styles.transformSources}>from: {srcFields.join(' + ')}</div>
                            </td>
                            <td className={styles.transposeTdValue}>{formatValue(virtualFields[vfName]) || <em className={styles.textMuted}>null</em>}</td>
                            <td className={styles.transposeTd}>
                              <span className={sourceMap[vfName] && FRAMEWORK_FIELDS.has(sourceMap[vfName]) ? styles.mapsToFramework : styles.mapsToDss}>
                                → {sourceMap[vfName] || vfName}
                              </span>
                            </td>
                          </tr>
                        ))}

                        {/* Separator */}
                        {(directMapped.length > 0 || tmEntries.length > 0) && unmapped.length > 0 && (
                          <tr><td colSpan={3} className={styles.separator} /></tr>
                        )}

                        {/* Unmapped fields */}
                        {unmapped.map(field => (
                          <tr key={field}>
                            <td className={`${styles.transposeTd} ${styles.textMuted}`}><span>{field}</span></td>
                            <td className={`${styles.transposeTdValue} ${styles.textMuted}`}>{formatValue(raw[field]) || <em>null</em>}</td>
                            <td className={styles.transposeTd}><span className={styles.textMuted}>—</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        /* ─── Fields View ─── */
        <div className={styles.list}>
          {unmappedFramework.length > 0 && (
            <div className={styles.unmappedBar}>
              <span className={styles.unmappedLabel}>Not mapped:</span>
              {unmappedFramework.map(f => (
                <span key={f} className={styles.unmappedField}>{f}</span>
              ))}
            </div>
          )}

          {pagedFieldStats.map((field: any) => {
            const isExpanded = expandedFields.has(field.path);
            const isMapped = !!field.mapped_to;
            const isFramework = isMapped && FRAMEWORK_FIELDS.has(field.mapped_to);
            const coverageColor = getCoverageColor(field.coverage);

            return (
              <div
                key={field.path}
                className={`${styles.fieldCard} ${isExpanded ? styles.recordExpanded : ''} ${isMapped ? (isFramework ? styles.fieldMappedFramework : styles.fieldMappedDss) : ''}`}
              >
                <div className={styles.fieldRow} onClick={() => toggleField(field.path)}>
                  <div className={styles.fieldInfo}>
                    <span className={styles.fieldPath}>{field.path}</span>
                    <span className={styles.fieldTypeBadge} style={typeBadgeStyle(field.type)}>{field.type}</span>
                    {field.is_virtual && <span className={styles.fieldVirtualBadge}>transformed</span>}
                  </div>
                  <span className={styles.fieldMetaItem}>{field.unique_count || 0} unique</span>
                  <span className={styles.fieldMetaItem} style={{ color: coverageColor }}>{field.coverage}%</span>
                  <span className={styles.fieldMapping}>
                    {isMapped ? (
                      <span className={isFramework ? styles.mapsToFramework : styles.mapsToDss}>→ {field.mapped_to}</span>
                    ) : (
                      <span className={styles.textMuted}>—</span>
                    )}
                  </span>
                  <svg className={`${styles.chevron} ${isExpanded ? styles.chevronOpen : ''}`} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="6 9 12 15 18 9"/></svg>
                </div>

                {isExpanded && (
                  <div className={styles.fieldBody}>
                    {field.transform_sources && (
                      <div className={styles.transformSources}>
                        Sources: {field.transform_sources.map((s: string) => <span key={s}>{s}</span>)}
                      </div>
                    )}
                    {field.top_values?.length > 0 && (
                      <div className={styles.valueSection}>
                        <div className={styles.valueSectionTitle}>Most common</div>
                        {field.top_values.map((v: any, i: number) => (
                          <div key={i} className={styles.valueRow}>
                            <span className={styles.valueText}>{v.value || <em>(empty)</em>}</span>
                            <span className={styles.valueCount}>{v.count}x</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {field.rare_values?.length > 0 && (
                      <div className={styles.valueSection}>
                        <div className={styles.valueSectionTitle}>Rarest</div>
                        {field.rare_values.map((v: any, i: number) => (
                          <div key={i} className={styles.valueRow}>
                            <span className={styles.valueText}>{v.value || <em>(empty)</em>}</span>
                            <span className={styles.valueCount}>{v.count}x</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Pagination */}
      {(totalPages > 1 || pageSize !== PAGE_SIZE) && (
        <div className={styles.pagination}>
          <div className={styles.pageSizeGroup}>
            <select
              className={styles.pageSizeSelect}
              value={pageSize}
              onChange={e => { setPageSize(Number(e.target.value)); setOffset(0); }}
            >
              {[10, 25, 50].map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <span className={styles.pageInfo}>per page</span>
          </div>
          <div className={styles.pageNavGroup}>
            <Button variant="secondary" size="sm" disabled={currentPage === 1} onClick={() => setOffset(offset - pageSize)}>
              Prev
            </Button>
            <span className={styles.pageInfo}>{currentPage} / {totalPages}</span>
            <Button variant="secondary" size="sm" disabled={currentPage === totalPages} onClick={() => setOffset(offset + pageSize)}>
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
