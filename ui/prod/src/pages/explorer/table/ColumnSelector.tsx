// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import type { ExplorerFieldsResponse } from '@/api/assets';
import { getDisplayName, groupByCategory } from '../explorer-utils';
import {
  ASSET_LABEL_COLUMN_KEY,
  ASSET_TABLE_DEFAULT_COLUMNS,
} from '@/constants/assetTableColumns';
import styles from './table.module.css';

interface ColumnSelectorProps {
  isOpen: boolean;
  onClose: () => void;
  fields: ExplorerFieldsResponse;
  visibleColumns: Record<string, boolean>;
  onApply: (visibleColumns: Record<string, boolean>) => void;
  /** Ref to the trigger button. Clicks on it are ignored by the
   *  click-outside detector so the trigger can toggle the panel. */
  triggerRef?: React.RefObject<HTMLElement | null>;
}

interface FieldGroup {
  label: string;
  fields: { key: string; name: string }[];
  defaultExpanded: boolean;
}

export function ColumnSelector({
  isOpen,
  onClose,
  fields,
  visibleColumns,
  onApply,
  triggerRef,
}: ColumnSelectorProps) {
  const [localVisible, setLocalVisible] = useState<Record<string, boolean>>({});
  const [search, setSearch] = useState('');
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const panelRef = useRef<HTMLDivElement>(null);

  const groups: FieldGroup[] = useMemo(() => {
    const buckets = groupByCategory(fields.entries);
    const out: FieldGroup[] = [];

    const toItem = (f: { encoded_key: string; display_name: string }) => ({
      key: f.encoded_key,
      name: getDisplayName(f),
    });

    if (buckets.standard.length > 0) {
      out.push({
        label: 'Standard Fields',
        fields: buckets.standard.map(toItem),
        defaultExpanded: true,
      });
    }
    if (buckets.advanced.length > 0) {
      out.push({
        label: 'Advanced Fields',
        fields: buckets.advanced.map(toItem),
        defaultExpanded: false,
      });
    }
    for (const [source, sourceFields] of Object.entries(buckets.extended)) {
      if (sourceFields.length > 0) {
        out.push({
          label: source,
          fields: sourceFields.map(toItem),
          defaultExpanded: false,
        });
      }
    }
    if (buckets.posture.length > 0) {
      out.push({
        label: 'Posture',
        fields: buckets.posture.map(toItem),
        defaultExpanded: false,
      });
    }
    if (buckets.tags.length > 0) {
      out.push({
        label: 'Tags',
        fields: buckets.tags.map(toItem),
        defaultExpanded: false,
      });
    }
    return out;
  }, [fields]);

  // Sync local state when panel opens
  useEffect(() => {
    if (isOpen) {
      setLocalVisible({ ...visibleColumns });
      setSearch('');
      const initial: Record<string, boolean> = {};
      for (const g of groups) {
        initial[g.label] = g.defaultExpanded;
      }
      setExpandedGroups(initial);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  // Close on click outside
  useEffect(() => {
    if (!isOpen) return;
    const onMouseDown = (e: MouseEvent) => {
      const target = e.target as Node;
      // Ignore clicks inside the panel itself…
      if (panelRef.current && panelRef.current.contains(target)) return;
      // …and clicks on the trigger button. The trigger's own onClick handles
      // toggling — without this guard, the click closes here and the trigger
      // immediately reopens.
      if (triggerRef?.current && triggerRef.current.contains(target)) return;
      onClose();
    };
    // Use setTimeout so the click that opens the panel doesn't immediately close it
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', onMouseDown);
    }, 0);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('mousedown', onMouseDown);
    };
  }, [isOpen, onClose, triggerRef]);

  const toggleGroup = useCallback((label: string) => {
    setExpandedGroups((prev) => ({ ...prev, [label]: !prev[label] }));
  }, []);

  const toggleField = useCallback((key: string) => {
    // The pinned Asset Label column can never be hidden (#1153).
    if (key === ASSET_LABEL_COLUMN_KEY) return;
    setLocalVisible((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const handleApply = useCallback(() => {
    const hasAny = Object.values(localVisible).some(Boolean);
    if (!hasAny) return; // keep at least one
    onApply(localVisible);
    onClose();
  }, [localVisible, onApply, onClose]);

  const handleReset = useCallback(() => {
    setLocalVisible(Object.fromEntries(ASSET_TABLE_DEFAULT_COLUMNS.map((k) => [k, true])));
  }, []);

  if (!isOpen) return null;

  const query = search.toLowerCase();
  const isSearching = query.length > 0;

  return (
    <div ref={panelRef} className={styles.columnSelectorPanel}>
      <input
        type="text"
        className={styles.columnSelectorSearch}
        placeholder="Search columns..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        autoFocus
      />

      <div className={styles.columnSelectorList}>
        {groups.map((group) => {
          const filteredFields = isSearching
            ? group.fields.filter((f) => f.name.toLowerCase().includes(query))
            : group.fields;

          if (isSearching && filteredFields.length === 0) return null;

          const isExpanded = isSearching ? true : (expandedGroups[group.label] ?? group.defaultExpanded);

          return (
            <div key={group.label} className={styles.columnGroup}>
              <div
                className={`${styles.columnGroupHeader} ${!isExpanded ? styles.columnGroupCollapsed : ''}`}
                onClick={() => toggleGroup(group.label)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') toggleGroup(group.label);
                }}
              >
                <svg
                  className={styles.columnGroupChevron}
                  viewBox="0 0 12 12"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <polyline points="2 4 6 8 10 4" />
                </svg>
                {group.label}
              </div>

              {isExpanded && (
                <div className={styles.columnGroupItems}>
                  {filteredFields.map((f) => {
                    // The pinned Asset Label column is always shown and can't
                    // be switched off — render it disabled with a hint (#1153).
                    const isPinned = f.key === ASSET_LABEL_COLUMN_KEY;
                    return (
                      <label key={f.key} className={styles.columnItem}>
                        <input
                          type="checkbox"
                          className={styles.columnItemCheckbox}
                          checked={isPinned || !!localVisible[f.key]}
                          disabled={isPinned}
                          onChange={() => toggleField(f.key)}
                        />
                        {f.name}
                        {isPinned && (
                          <span className={styles.columnItemHint}>Always first</span>
                        )}
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className={styles.columnSelectorFooter}>
        <button
          className={styles.columnSelectorClearBtn}
          onClick={handleReset}
          type="button"
        >
          Reset
        </button>
        <button
          className={styles.columnSelectorApplyBtn}
          onClick={handleApply}
          type="button"
        >
          Apply
        </button>
      </div>
    </div>
  );
}
