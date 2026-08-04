// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useRef, useMemo } from 'react';
import { FRAMEWORK_FIELDS, getNestedValue, inferFieldType, TYPE_COLORS } from '@/pages/integrations/integration-utils';
import { Button } from '@/components/ui/Button';
import type { WizardFormData } from './wizard-state';
import { applyTransformPipeline } from './transform-pipeline';
import styles from './FieldMappingStep.module.css';

/** Typeahead input — filters options as you type, selects on click or Enter */

interface FieldMappingStepProps {
  formData: WizardFormData;
  onUpdate: (partial: Partial<WizardFormData>) => void;
}

interface FieldMeta {
  path: string;
  type: string;
  coverage: number;
  sample: string;
  uniqueCount: number;
  topValues: { value: string; count: number }[];
  rareValues: { value: string; count: number }[];
}


const IDENTIFIER_FIELDS = new Set(['hostname', 'fqdn', 'ip', 'mac']);
const DSS_LABEL = 'Data Source Specific';
const SOURCE_CARD_HEIGHT = 54;

function filterFrameworkFields(fields: typeof FRAMEWORK_FIELDS, query: string) {
  if (!query) return fields;
  const q = query.toLowerCase();
  return fields.filter(f =>
    f.label.toLowerCase().includes(q) ||
    f.key.toLowerCase().includes(q) ||
    (f.description || '').toLowerCase().includes(q)
  );
}

const FIELD_ALIASES: Record<string, string[]> = {
  hostname: ['computername', 'devicename', 'name', 'cn', 'host', 'computer_name', 'device_name'],
  fqdn: ['fully_qualified_domain_name', 'dns_name', 'dnsname'],
  ip: ['ip_address', 'ipaddress', 'ip_addr', 'ipv4', 'ipv4_address'],
  mac: ['mac_address', 'macaddress', 'mac_addr', 'physical_address'],
  sn: ['serial_number', 'serialnumber', 'serial', 'serial_no'],
  os: ['operating_system', 'os_name', 'os_version', 'platform'],
  manufacturer: ['make', 'vendor', 'brand'],
  model: ['model_name', 'product_name', 'device_model'],
  vendor_category: ['device_type', 'asset_type', 'category', 'type'],
};

type FieldMappingEntry = WizardFormData['field_mapping'][number];

function autoMatchFields(sourceFields: string[], existingMappings: FieldMappingEntry[]): FieldMappingEntry[] {
  if (existingMappings.length > 0) return existingMappings;
  const mappings: FieldMappingEntry[] = [];
  for (const [target, aliases] of Object.entries(FIELD_ALIASES)) {
    const allNames = [target, ...aliases];
    const match = sourceFields.find(sf => {
      const leaf = sf.toLowerCase().split('.').pop() || '';
      return allNames.includes(leaf);
    });
    if (match) mappings.push({ source: match, target });
  }
  return mappings;
}

export function FieldMappingStep({ formData, onUpdate }: FieldMappingStepProps) {
  const [search, setSearch] = useState('');
  const [expandedFields, setExpandedFields] = useState<Set<string>>(new Set());
  const [mappingOpenField, setMappingOpenField] = useState<string | null>(null);
  const [targetSearch, setTargetSearch] = useState('');
  const [targetMappingOpen, setTargetMappingOpen] = useState<string | null>(null);
  const [sourceSearchForTarget, setSourceSearchForTarget] = useState('');
  const [targetColumnSearch, setTargetColumnSearch] = useState('');
  const [dssSearch, setDssSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [dssDropdownOpen, setDssDropdownOpen] = useState(false);
  const [dssPos, setDssPos] = useState<{ bottom: number; left: number; width: number } | null>(null);
  const dssWrapperRef = useRef<HTMLDivElement>(null);
  const dssDropdownRef = useRef<HTMLDivElement>(null);
  const [sourcePage, setSourcePage] = useState(0);
  const targetColumnRef = useRef<HTMLDivElement>(null);
  const autoMatched = useRef(false);

  const [sourcePageSize, setSourcePageSize] = useState(12);

  useEffect(() => {
    if (!targetColumnRef.current) return;
    const observer = new ResizeObserver(([entry]) => {
      const available = entry.contentRect.height - 60; // subtract title + search + pagination bar
      const fits = Math.max(4, Math.floor(available / SOURCE_CARD_HEIGHT));
      setSourcePageSize(fits);
    });
    observer.observe(targetColumnRef.current);
    return () => observer.disconnect();
  }, []);

  const toggleExpand = (field: string) => {
    setExpandedFields(prev => {
      const next = new Set(prev);
      if (next.has(field)) next.delete(field);
      else next.add(field);
      return next;
    });
  };


  const toggleMappingPanel = (field: string) => {
    if (mappingOpenField === field) {
      setMappingOpenField(null);
    } else {
      setMappingOpenField(field);
      setTargetMappingOpen(null);
      setSourceSearchForTarget('');
    }
    setTargetSearch('');
  };
  const filteredTargets = useMemo(
    () => filterFrameworkFields(FRAMEWORK_FIELDS, targetSearch),
    [targetSearch]
  );

  const filteredFrameworkFields = useMemo(
    () => filterFrameworkFields(FRAMEWORK_FIELDS, targetColumnSearch),
    [targetColumnSearch]
  );

  useEffect(() => {
    if (!autoMatched.current && formData.sourceFields?.length && formData.field_mapping.length === 0) {
      autoMatched.current = true;
      const matches = autoMatchFields(formData.sourceFields, formData.field_mapping);
      if (matches.length > 0) {
        onUpdate({ field_mapping: matches });
      }
    }
  }, [formData.sourceFields]);


  // Close DSS dropdown on click outside or Escape
  useEffect(() => {
    if (!dssDropdownOpen) return;
    const handleClick = (e: MouseEvent) => {
      const target = e.target as Node;
      const inWrapper = dssWrapperRef.current?.contains(target);
      const inDropdown = dssDropdownRef.current?.contains(target);
      if (!inWrapper && !inDropdown) {
        setDssDropdownOpen(false);
        setDssSearch('');
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setDssDropdownOpen(false); setDssSearch(''); }
    };
    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleKey);
    };
  }, [dssDropdownOpen]);

  // Raw source field statistics — stable once connection test completes
  const rawFieldMetadata = useMemo<Map<string, FieldMeta>>(() => {
    const map = new Map<string, FieldMeta>();
    const records = formData.sampleData || [];
    const fields = formData.sourceFields || [];
    if (!records.length) return map;

    for (const path of fields) {
      let nonNullCount = 0;
      let sample = '';
      let firstValue: unknown = null;
      const valueCounts = new Map<string, number>();

      for (const record of records.slice(0, 100)) {
        const value = getNestedValue(record, path);
        if (value != null && value !== '') {
          nonNullCount++;
          if (firstValue === null) firstValue = value;
          const str = String(value).slice(0, 100);
          if (!sample) sample = str.slice(0, 60);
          valueCounts.set(str, (valueCounts.get(str) || 0) + 1);
        }
      }

      const sorted = [...valueCounts.entries()]
        .map(([value, count]) => ({ value, count }))
        .sort((a, b) => b.count - a.count);

      map.set(path, {
        path,
        type: inferFieldType(firstValue),
        coverage: records.length > 0 ? Math.round((nonNullCount / Math.min(records.length, 100)) * 100) : 0,
        sample,
        uniqueCount: valueCounts.size,
        topValues: sorted.slice(0, 5),
        rareValues: sorted.length > 5 ? sorted.slice(-3).reverse() : [],
      });
    }
    return map;
  }, [formData.sampleData, formData.sourceFields]);

  // Virtual field metadata — cheap, re-runs when transformations change
  const virtualFieldMetadata = useMemo<Map<string, FieldMeta>>(() => {
    const map = new Map<string, FieldMeta>();
    const records = formData.sampleData || [];
    for (const t of formData.transformations) {
      let nonNullCount = 0;
      let sample = '';
      let firstValue: unknown = null;
      const valueCounts = new Map<string, number>();

      for (const record of records.slice(0, 100)) {
        const result = applyTransformPipeline(t.operations, record);
        const value = result.finalResult;
        if (value != null && value !== '') {
          nonNullCount++;
          if (firstValue === null) firstValue = value;
          const str = String(value).slice(0, 100);
          if (!sample) sample = str.slice(0, 60);
          valueCounts.set(str, (valueCounts.get(str) || 0) + 1);
        }
      }

      const sorted = [...valueCounts.entries()]
        .map(([value, count]) => ({ value, count }))
        .sort((a, b) => b.count - a.count);

      map.set(t.name, {
        path: t.name,
        type: inferFieldType(firstValue),
        coverage: records.length > 0 ? Math.round((nonNullCount / Math.min(records.length, 100)) * 100) : 0,
        sample,
        uniqueCount: valueCounts.size,
        topValues: sorted.slice(0, 5),
        rareValues: sorted.length > 5 ? sorted.slice(-3).reverse() : [],
      });
    }
    return map;
  }, [formData.transformations, formData.sampleData]);

  const virtualFieldNames = useMemo(
    () => new Set(formData.transformations.map(t => t.name)),
    [formData.transformations],
  );

  const fieldMetadata = useMemo(
    () => new Map([...virtualFieldMetadata, ...rawFieldMetadata]),
    [rawFieldMetadata, virtualFieldMetadata],
  );

  // Distinct types present in the source fields, sorted by count
  const availableTypes = useMemo(() => {
    const counts = new Map<string, number>();
    for (const [path, meta] of fieldMetadata) {
      if (!virtualFieldNames.has(path)) {
        counts.set(meta.type, (counts.get(meta.type) || 0) + 1);
      }
    }
    const result = [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([type, count]) => ({ type, count }));
    if (virtualFieldNames.size > 0) {
      result.push({ type: 'virtual', count: virtualFieldNames.size });
    }
    return result;
  }, [fieldMetadata, virtualFieldNames]);

  // Merge: virtual fields first, then raw source fields
  const sourceFields = useMemo(
    () => [...formData.transformations.map(t => t.name), ...(formData.sourceFields ?? [])],
    [formData.transformations, formData.sourceFields],
  );

  const filteredSourcesForTarget = useMemo(
    () => sourceSearchForTarget
      ? sourceFields.filter(f => f.toLowerCase().includes(sourceSearchForTarget.toLowerCase()))
      : sourceFields,
    [sourceFields, sourceSearchForTarget]
  );

  const filteredFields = useMemo(() => {
    let fields = sourceFields;
    if (search) fields = fields.filter((f) => f.toLowerCase().includes(search.toLowerCase()));
    if (typeFilter) {
      if (typeFilter === 'virtual') {
        fields = fields.filter(f => virtualFieldNames.has(f));
      } else {
        fields = fields.filter((f) => fieldMetadata.get(f)?.type === typeFilter);
      }
    }
    return fields;
  }, [sourceFields, search, typeFilter, fieldMetadata, virtualFieldNames]);

  // Reset page when search or filter changes
  useEffect(() => { setSourcePage(0); }, [search, typeFilter]);

  const totalSourcePages = Math.max(1, Math.ceil(filteredFields.length / sourcePageSize));
  const pagedFields = useMemo(
    () => filteredFields.slice(sourcePage * sourcePageSize, (sourcePage + 1) * sourcePageSize),
    [filteredFields, sourcePage, sourcePageSize]
  );

  const mappedSources = useMemo(
    () => new Set(formData.field_mapping.map((m) => m.source)),
    [formData.field_mapping]
  );


  // Add a source to a target (many-to-one — appends, doesn't replace).
  // A source field is mutually exclusive between field_mapping (mapped to a
  // framework target) and preserve_fields (DSS) — adding to one drops it
  // from the other so the UI state stays consistent with what the badge
  // shows.
  const handleAddSourceToTarget = (target: string, source: string) => {
    if (!source) return;
    let updated = formData.field_mapping.filter((m) => m.source !== source);
    updated.push({ source, target });
    const partial: Partial<WizardFormData> = { field_mapping: updated };
    if (formData.preserve_fields.includes(source)) {
      partial.preserve_fields = formData.preserve_fields.filter(f => f !== source);
    }
    onUpdate(partial);
  };

  // Remove a single source from a target
  const handleRemoveSourceFromTarget = (target: string, source: string) => {
    const updated = formData.field_mapping.filter((m) => !(m.target === target && m.source === source));
    onUpdate({ field_mapping: updated });
  };

  const addPreserveField = (field: string) => {
    if (formData.preserve_fields.includes(field)) return;
    const partial: Partial<WizardFormData> = {
      preserve_fields: [...formData.preserve_fields, field],
    };
    if (formData.field_mapping.some(m => m.source === field)) {
      partial.field_mapping = formData.field_mapping.filter(m => m.source !== field);
    }
    onUpdate(partial);
  };

  const removePreserveField = (field: string) => {
    onUpdate({ preserve_fields: formData.preserve_fields.filter(f => f !== field) });
  };

  const preservedSet = useMemo(
    () => new Set(formData.preserve_fields),
    [formData.preserve_fields]
  );

  // Precomputed target→sources lookup (single O(n) pass, replaces per-render linear scans)
  const sourcesByTarget = useMemo(
    () => formData.field_mapping.reduce<Record<string, string[]>>((acc, m) => {
      (acc[m.target] ??= []).push(m.source);
      return acc;
    }, {}),
    [formData.field_mapping]
  );

  const hasIdentifier = useMemo(
    () => formData.field_mapping.some(m => IDENTIFIER_FIELDS.has(m.target)),
    [formData.field_mapping]
  );

  const unmappedFields = useMemo(
    () => sourceFields.filter(f => !mappedSources.has(f)),
    [sourceFields, mappedSources]
  );

  if (sourceFields.length === 0) {
    return (
      <div className={styles.emptyState}>
        Run the connection test first to load source fields
      </div>
    );
  }

  return (
    <div>
      <div className={styles.filterBar}>
        <div className={styles.filterPills}>
          <button
            type="button"
            className={`${styles.filterPill} ${typeFilter === null ? styles.filterPillActive : ''}`}
            onClick={() => setTypeFilter(null)}
          >
            All <span className={styles.filterCount}>{sourceFields.length}</span>
          </button>
          {availableTypes.map(({ type, count }) => {
            const color = TYPE_COLORS[type] ?? '#6b7280';
            const isActive = typeFilter === type;
            return (
              <button
                key={type}
                type="button"
                className={`${styles.filterPill} ${isActive ? styles.filterPillActive : ''}`}
                onClick={() => setTypeFilter(isActive ? null : type)}
                style={{
                  borderColor: color,
                  color: color,
                  ...(isActive && { background: `${color}1f` }),
                }}
              >
                {type.replace('_', ' ')} <span className={styles.filterCount}>{count}</span>
              </button>
            );
          })}
        </div>
        <span className={`${styles.statusWarning} ${hasIdentifier ? styles.statusWarningMuted : ''}`}>
          Map at least one identifier
        </span>
        {formData.field_mapping.length > 0 && (
          <Button className={styles.clearAllBtn} variant="secondary" size="sm" onClick={() => onUpdate({ field_mapping: [] })}>
            Clear All
          </Button>
        )}
      </div>

      <div className={styles.mappingLayout}>
        {/* Left: source fields */}
        <div>
          <div className={styles.columnTitle}>Source Fields</div>
          <input
            type="text"
            className={styles.searchInput}
            placeholder="Search fields..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className={styles.sourceList}>
            {pagedFields.map((field) => {
              const isPreserved = preservedSet.has(field);
              const isMapped = mappedSources.has(field) || isPreserved;
              const meta = fieldMetadata.get(field);
              const mappedTarget = formData.field_mapping.find((m) => m.source === field)?.target || '';
              const isVirtual = virtualFieldNames.has(field);
              const cardClass = [
                styles.sourceCard,
                isMapped ? styles.cardMapped : '',
                isVirtual ? styles.cardVirtual : '',
              ]
                .filter(Boolean)
                .join(' ');

              const coverageColor =
                !meta ? 'var(--text-tertiary)' :
                meta.coverage >= 90 ? '#10b981' :
                meta.coverage >= 50 ? '#f59e0b' :
                '#ef4444';

              const isExpanded = expandedFields.has(field);

              const isMappingOpen = mappingOpenField === field;
              const mappedTargetField = FRAMEWORK_FIELDS.find(f => f.key === mappedTarget);

              return (
                <div key={field} className={cardClass}>
                  {/* Header row — click field name to toggle mapping panel */}
                  <div className={styles.sourceHeader}>
                    <div className={styles.sourceField} onClick={() => toggleMappingPanel(field)} title="Click to map this field">
                      {field}
                    </div>
                    {(mappedTargetField || isPreserved) && (
                      <span className={styles.chip}>
                        <span className={styles.chipText}>
                          → {mappedTargetField?.label ?? DSS_LABEL}
                        </span>
                      </span>
                    )}
                    {isVirtual && <span className={styles.virtualBadge}>TRANSFORMED</span>}
                    {meta && (
                      <span className={styles.typeBadge} style={{ color: TYPE_COLORS[meta.type] ?? '#6b7280' }}>
                        {meta.type}
                      </span>
                    )}
                    {meta && (
                      <>
                        <div className={styles.coverageBar}>
                          <div className={styles.coverageFill} style={{ width: `${meta.coverage}%`, background: coverageColor }} />
                        </div>
                        <span className={styles.coverageText}>{meta.coverage}%</span>
                      </>
                    )}
                    {meta && (
                      <button type="button" className={`${styles.expandButton} ${isExpanded ? styles.expandButtonOpen : ''}`} onClick={(e) => { e.stopPropagation(); toggleExpand(field); }} title="View collected values">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
                        <span className={styles.expandLabel}>Values</span>
                      </button>
                    )}
                  </div>

                  {/* Mapping panel — shown when field name is clicked */}
                  {isMappingOpen && (
                    <div className={styles.mappingPanel}>
                      <div className={styles.mappingPanelHeader}>
                        <span className={styles.mappingPanelTitle}>Map to target field:</span>
                        <button type="button" className={styles.mappingPanelClose} onClick={() => setMappingOpenField(null)}>
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
                        </button>
                      </div>
                      <input
                        type="text"
                        className={styles.mappingPanelSearch}
                        placeholder="Search target fields..."
                        value={targetSearch}
                        onChange={(e) => setTargetSearch(e.target.value)}
                        autoFocus
                      />
                      <div className={styles.mappingPanelList}>
                        {filteredTargets.map(f => {
                          const isSelected = (sourcesByTarget[f.key] ?? []).includes(field);
                          const existingSources = sourcesByTarget[f.key] ?? [];
                          const isIdentifier = IDENTIFIER_FIELDS.has(f.key);
                          return (
                            <button
                              key={f.key}
                              type="button"
                              className={`${styles.mappingPanelOption} ${isSelected ? styles.mappingPanelOptionActive : ''}`}
                              onClick={() => {
                                if (isSelected) {
                                  handleRemoveSourceFromTarget(f.key, field);
                                } else {
                                  handleAddSourceToTarget(f.key, field);
                                }
                                setMappingOpenField(null);
                                setTargetSearch('');
                              }}
                            >
                              <span className={styles.mappingPanelOptionName}>
                                {f.label} {isIdentifier && <span className={styles.required}>*</span>}
                              </span>
                              <span className={styles.mappingPanelOptionDesc}>{f.description}</span>
                              {existingSources.length > 0 && !isSelected && (
                                <span className={styles.mappingPanelOptionTaken}>
                                  (mapped: {existingSources.join(', ')})
                                </span>
                              )}
                            </button>
                          );
                        })}
                        {(!targetSearch || DSS_LABEL.toLowerCase().includes(targetSearch.toLowerCase())) && (
                          <>
                            <div className={styles.mappingPanelDivider} />
                            <button
                              type="button"
                              className={`${styles.mappingPanelOption} ${styles.mappingPanelOptionDss} ${preservedSet.has(field) ? styles.mappingPanelOptionActive : ''}`}
                              onClick={() => {
                                if (preservedSet.has(field)) {
                                  removePreserveField(field);
                                } else {
                                  addPreserveField(field);
                                }
                                setMappingOpenField(null);
                              }}
                            >
                              <span className={styles.mappingPanelOptionName}>{DSS_LABEL}</span>
                              <span className={styles.mappingPanelOptionDesc}>Preserve as additional field (agent versions, IDs, etc.)</span>
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Expanded details — shown when info icon is clicked */}
                  {isExpanded && meta && (
                    <div className={styles.expandedBody}>
                      <div className={styles.expandedRow}>
                        <span className={styles.expandedLabel}>Unique values</span>
                        <span className={styles.expandedValue}>{meta.uniqueCount}</span>
                      </div>
                      {meta.topValues.length > 0 && (
                        <div className={styles.expandedSection}>
                          <span className={styles.expandedLabel}>Top values</span>
                          <div className={styles.valueList}>
                            {meta.topValues.map((v, i) => (
                              <div key={i} className={styles.valueRow}>
                                <span className={styles.valueText}>{v.value}</span>
                                <span className={styles.valueCount}>{v.count}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {meta.rareValues.length > 0 && (
                        <div className={styles.expandedSection}>
                          <span className={styles.expandedLabel}>Rare values</span>
                          <div className={styles.valueList}>
                            {meta.rareValues.map((v, i) => (
                              <div key={i} className={styles.valueRow}>
                                <span className={styles.valueText}>{v.value}</span>
                                <span className={styles.valueCount}>{v.count}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          {totalSourcePages > 1 && (
            <div className={styles.sourcePagination}>
              <button type="button" className={styles.pageButton} disabled={sourcePage === 0} onClick={() => setSourcePage(p => p - 1)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 18l-6-6 6-6"/></svg>
              </button>
              <span className={styles.pageInfo}>{sourcePage + 1} / {totalSourcePages}</span>
              <button type="button" className={styles.pageButton} disabled={sourcePage >= totalSourcePages - 1} onClick={() => setSourcePage(p => p + 1)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18l6-6-6-6"/></svg>
              </button>
            </div>
          )}
        </div>

        {/* Right: target fields + data source specific */}
        <div ref={targetColumnRef}>
          <div className={styles.columnTitle}>Target Fields</div>
          <input
            type="text"
            className={styles.searchInput}
            placeholder="Search target fields..."
            value={targetColumnSearch}
            onChange={(e) => setTargetColumnSearch(e.target.value)}
          />
          <div className={styles.targetList}>
            {filteredFrameworkFields.map((field) => {
              const sources = sourcesByTarget[field.key] ?? [];
              const hasMappings = sources.length > 0;
              const isIdentifier = IDENTIFIER_FIELDS.has(field.key);
              const isTargetPanelOpen = targetMappingOpen === field.key;

              return (
                <div key={field.key} className={`${styles.sourceCard} ${styles.targetCard} ${hasMappings ? styles.cardMapped : ''}`} onClick={() => {
                      if (isTargetPanelOpen) {
                        setTargetMappingOpen(null);
                        setSourceSearchForTarget('');
                      } else {
                        setTargetMappingOpen(field.key);
                        setSourceSearchForTarget('');
                        setMappingOpenField(null);
                      }
                    }} title="Click to map source fields">
                  <div className={styles.targetCardTop}>
                    <div className={styles.sourceField}>
                      {field.label} {isIdentifier && <span className={styles.required}>*</span>}
                    </div>
                    {hasMappings && (
                      <div className={styles.chipRow} onClick={(e) => e.stopPropagation()}>
                        {sources.map(src => (
                          <span key={src} className={styles.chip}>
                            <span className={styles.chipText}>{src}</span>
                            <button type="button" className={styles.chipRemove} onClick={() => handleRemoveSourceFromTarget(field.key, src)} title="Remove">
                              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className={styles.targetCardDesc}>{field.description}</div>

                  {/* Source search panel — click field name to open */}
                  {isTargetPanelOpen && (
                    <div className={styles.mappingPanel}>
                      <div className={styles.mappingPanelHeader}>
                        <span className={styles.mappingPanelTitle}>{hasMappings ? 'Add another source field:' : 'Map source field:'}</span>
                        <button type="button" className={styles.mappingPanelClose} onClick={() => { setTargetMappingOpen(null); setSourceSearchForTarget(''); }}>
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
                        </button>
                      </div>
                      <input
                        type="text"
                        className={styles.mappingPanelSearch}
                        placeholder="Search source fields..."
                        value={sourceSearchForTarget}
                        onChange={(e) => setSourceSearchForTarget(e.target.value)}
                        autoFocus
                      />
                      <div className={styles.mappingPanelList}>
                        {filteredSourcesForTarget.map(sf => {
                          const isAlreadyMapped = sources.includes(sf);
                          const meta = fieldMetadata.get(sf);
                          return (
                            <button
                              key={sf}
                              type="button"
                              className={`${styles.mappingPanelOption} ${isAlreadyMapped ? styles.mappingPanelOptionActive : ''}`}
                              onClick={() => {
                                if (isAlreadyMapped) {
                                  handleRemoveSourceFromTarget(field.key, sf);
                                } else {
                                  handleAddSourceToTarget(field.key, sf);
                                }
                              }}
                            >
                              <span className={styles.mappingPanelOptionName}>{sf}</span>
                              {meta && (
                                <span className={styles.mappingPanelOptionDesc}>
                                  {meta.type} &middot; {meta.coverage}% coverage{meta.sample ? ` · "${meta.sample.slice(0, 30)}"` : ''}
                                </span>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Data Source Specific Fields — dropdown selector */}
          {unmappedFields.length > 0 && (
            <div className={styles.dssCard}>
              <div className={styles.dssCardTitle}>Data Source Specific Fields</div>
              <div className={styles.dssDescription}>Select additional fields to preserve (agent versions, specific IDs, etc.)</div>

              {/* Selected preserve chips */}
              {formData.preserve_fields.length > 0 && (
                <div className={styles.dssChips}>
                  {formData.preserve_fields.map(field => (
                    <span key={field} className={styles.chip}>
                      <span className={styles.chipText}>{field}</span>
                      <button type="button" className={styles.chipRemove} onClick={() => removePreserveField(field)} title="Remove">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
                      </button>
                    </span>
                  ))}
                </div>
              )}

              {/* Search input that opens dropdown */}
              <div className={styles.dssWrapper} ref={dssWrapperRef}>
                <input
                  type="text"
                  className={styles.searchInput}
                  placeholder="Search fields to preserve..."
                  value={dssSearch}
                  onChange={(e) => { setDssSearch(e.target.value); if (!dssDropdownOpen) { setDssDropdownOpen(true); if (dssWrapperRef.current) { const r = dssWrapperRef.current.getBoundingClientRect(); setDssPos({ bottom: window.innerHeight - r.top, left: r.left, width: r.width }); } } }}
                  onFocus={() => { setDssDropdownOpen(true); if (dssWrapperRef.current) { const r = dssWrapperRef.current.getBoundingClientRect(); setDssPos({ bottom: window.innerHeight - r.top, left: r.left, width: r.width }); } }}
                />
                {dssDropdownOpen && dssPos && (
                  <div ref={dssDropdownRef} className={styles.dssDropdown} style={{ bottom: dssPos.bottom, left: dssPos.left, width: dssPos.width }}>
                    <div className={styles.dssDropdownHeader}>
                      <span className={styles.mappingPanelTitle}>
                        {unmappedFields.filter(f => !preservedSet.has(f)).length} fields available
                      </span>
                      <button type="button" className={styles.mappingPanelClose} onClick={() => { setDssDropdownOpen(false); setDssSearch(''); }}>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
                      </button>
                    </div>
                    <div className={styles.dssDropdownList}>
                      {unmappedFields
                        .filter(f => !dssSearch || f.toLowerCase().includes(dssSearch.toLowerCase()))
                        .map(field => {
                          const meta = fieldMetadata.get(field);
                          const isPreserved = preservedSet.has(field);
                          return (
                            <button
                              key={field}
                              type="button"
                              className={`${styles.mappingPanelOption} ${isPreserved ? styles.mappingPanelOptionActive : ''}`}
                              onClick={() => isPreserved ? removePreserveField(field) : addPreserveField(field)}
                            >
                              <span className={styles.mappingPanelOptionName}>
                                {field}
                                {meta && (
                                  <span className={styles.typeBadge} style={{ color: TYPE_COLORS[meta.type] ?? '#6b7280', marginLeft: '6px' }}>
                                    {meta.type}
                                  </span>
                                )}
                              </span>
                              {meta && (
                                <span className={styles.mappingPanelOptionDesc}>{meta.coverage}% coverage{meta.sample ? ` · "${meta.sample.slice(0, 30)}"` : ''}</span>
                              )}
                            </button>
                          );
                        })}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
