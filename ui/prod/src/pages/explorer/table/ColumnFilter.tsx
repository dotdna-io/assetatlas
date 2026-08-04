// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { assetsApi } from '@/api/assets';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { isDateField } from '../explorer-utils';
import styles from './table.module.css';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ColumnFilterProps {
  field: string;
  dataType: string;
  selectedValues: string[];
  onApply: (field: string, values: string[]) => void;
  onClear: (field: string) => void;
  isOpen: boolean;
  onToggle: () => void;
}

interface FieldValue {
  value: string;
  count: number;
}

// ---------------------------------------------------------------------------
// Date filter sub-component
// ---------------------------------------------------------------------------

function DateFilter({
  field,
  currentDays,
  onApply,
  onClear,
}: {
  field: string;
  currentDays: number | null;
  onApply: (field: string, values: string[]) => void;
  onClear: (field: string) => void;
}) {
  const [days, setDays] = useState<number>(currentDays ?? 30);

  // Sync if the parent value changes (e.g. cleared externally)
  useEffect(() => {
    setDays(currentDays ?? 30);
  }, [currentDays]);

  function handleNumberChange(e: React.ChangeEvent<HTMLInputElement>) {
    let v = parseInt(e.target.value, 10);
    if (isNaN(v) || v < 1) v = 1;
    if (v > 365) v = 365;
    setDays(v);
  }

  function handleRangeChange(e: React.ChangeEvent<HTMLInputElement>) {
    setDays(parseInt(e.target.value, 10));
  }

  const PRESETS = [7, 30, 90];

  return (
    <div className={styles.dateFilterWrap}>
      <div className={styles.datePresets}>
        {PRESETS.map((p) => (
          <button
            key={p}
            className={`${styles.datePresetBtn} ${days === p ? styles.datePresetBtnActive : ''}`}
            onClick={() => setDays(p)}
            type="button"
          >
            {p}d
          </button>
        ))}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Last</span>
        <input
          type="number"
          min={1}
          max={365}
          value={days}
          onChange={handleNumberChange}
          style={{ width: 52, padding: '2px 6px', fontSize: 12, border: '1px solid var(--border)', borderRadius: 4 }}
        />
        <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>days</span>
      </div>
      <input
        type="range"
        min={1}
        max={365}
        value={days}
        onChange={handleRangeChange}
        style={{ width: '100%' }}
      />
      <div className={styles.filterActions}>
        <button
          className={styles.columnSelectorApplyBtn}
          onClick={() => onClear(field)}
          type="button"
          style={{ background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
        >
          Clear
        </button>
        <button
          className={styles.columnSelectorApplyBtn}
          onClick={() => onApply(field, [String(days)])}
          type="button"
        >
          Apply
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Value filter sub-component
// ---------------------------------------------------------------------------

function ValueFilter({
  field,
  selectedValues,
  onApply,
  onClear,
}: {
  field: string;
  selectedValues: string[];
  onApply: (field: string, values: string[]) => void;
  onClear: (field: string) => void;
}) {
  const [values, setValues] = useState<FieldValue[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [search, setSearch] = useState('');
  const [pending, setPending] = useState<string[]>(selectedValues);

  // Sync pending when selectedValues prop changes (external clear, etc.)
  useEffect(() => {
    setPending(selectedValues);
  }, [selectedValues]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    assetsApi
      .getFieldValues({ field, limit: 100 })
      .then((resp) => {
        if (!cancelled) {
          setValues(resp.values ?? []);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError(true);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [field]);

  const filtered = search
    ? values.filter((v) => v.value.toLowerCase().includes(search.toLowerCase()))
    : values;

  function toggle(value: string) {
    setPending((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value],
    );
  }

  // "Select all" operates on the currently-shown (search-filtered) values, and
  // is tri-state: checked when all of them are selected, indeterminate when
  // only some are. Toggling adds the filtered set or removes it.
  const filteredValues = filtered.map((v) => v.value);
  const allFilteredSelected =
    filteredValues.length > 0 && filteredValues.every((v) => pending.includes(v));
  const someFilteredSelected = filteredValues.some((v) => pending.includes(v));

  const selectAllRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = someFilteredSelected && !allFilteredSelected;
    }
  }, [someFilteredSelected, allFilteredSelected]);

  function toggleAll() {
    setPending((prev) => {
      const allSel =
        filteredValues.length > 0 && filteredValues.every((v) => prev.includes(v));
      if (allSel) {
        return prev.filter((v) => !filteredValues.includes(v));
      }
      const next = new Set(prev);
      filteredValues.forEach((v) => next.add(v));
      return Array.from(next);
    });
  }

  return (
    <>
      <input
        className={styles.filterSearch}
        placeholder="Search values..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        autoFocus
      />
      {!loading && !error && filtered.length > 0 && (
        <div
          className={styles.selectAllRow}
          onClick={toggleAll}
          role="checkbox"
          aria-checked={allFilteredSelected}
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              toggleAll();
            }
          }}
        >
          <input
            ref={selectAllRef}
            type="checkbox"
            checked={allFilteredSelected}
            onChange={toggleAll}
            onClick={(e) => e.stopPropagation()}
            style={{ accentColor: 'var(--accent)', flexShrink: 0 }}
          />
          <span>Select all</span>
        </div>
      )}
      <div className={styles.filterValueList}>
        {loading && (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 12 }}>
            <LoadingSpinner size="sm" />
          </div>
        )}
        {error && (
          <div style={{ padding: 8, fontSize: 12, color: 'var(--danger)' }}>
            Failed to load values
          </div>
        )}
        {!loading && !error && filtered.length === 0 && (
          <div style={{ padding: 8, fontSize: 12, color: 'var(--text-tertiary)' }}>
            No values found
          </div>
        )}
        {!loading &&
          !error &&
          filtered.map((v) => {
            const isSelected = pending.includes(v.value);
            return (
              <div
                key={v.value}
                className={styles.filterValue}
                onClick={() => toggle(v.value)}
                role="checkbox"
                aria-checked={isSelected}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    toggle(v.value);
                  }
                }}
              >
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => toggle(v.value)}
                  onClick={(e) => e.stopPropagation()}
                  style={{ accentColor: 'var(--accent)', flexShrink: 0 }}
                />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {v.value}
                </span>
                <span className={styles.filterValueCount}>{v.count}</span>
              </div>
            );
          })}
      </div>
      <div className={styles.filterActions}>
        <button
          className={styles.columnSelectorApplyBtn}
          onClick={() => {
            setPending([]);
            onClear(field);
          }}
          type="button"
          style={{ background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
        >
          Clear
        </button>
        <button
          className={styles.columnSelectorApplyBtn}
          onClick={() => onApply(field, pending)}
          type="button"
        >
          Apply
        </button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Main ColumnFilter component
// ---------------------------------------------------------------------------

export function ColumnFilter({
  field,
  dataType,
  selectedValues,
  onApply,
  onClear,
  isOpen,
  onToggle,
}: ColumnFilterProps) {
  const dropdownRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLSpanElement>(null);

  // Close on outside click or scroll
  useEffect(() => {
    if (!isOpen) return;
    function handleClick(e: MouseEvent) {
      const target = e.target as Node;
      if (dropdownRef.current && dropdownRef.current.contains(target)) return;
      // Ignore the trigger — its own onClick handles toggling.
      if (triggerRef.current && triggerRef.current.contains(target)) return;
      onToggle();
    }
    function handleScroll(e: Event) {
      if (dropdownRef.current && dropdownRef.current.contains(e.target as Node)) return;
      onToggle();
    }
    document.addEventListener('mousedown', handleClick);
    window.addEventListener('scroll', handleScroll, true);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      window.removeEventListener('scroll', handleScroll, true);
    };
  }, [isOpen, onToggle]);

  const isDate = isDateField(field, dataType);
  const hasFilter = selectedValues.length > 0;

  // For date mode, parse out the days value from selectedValues if present
  const currentDays: number | null = isDate && selectedValues.length > 0
    ? (parseInt(selectedValues[0], 10) || null)
    : null;

  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
      <span
        ref={triggerRef}
        className={`${styles.filterTrigger} ${hasFilter ? styles.filterTriggerActive : ''}`}
        onClick={(e) => {
          e.stopPropagation();
          onToggle();
        }}
        title={hasFilter ? 'Filter active' : 'Filter'}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            e.stopPropagation();
            onToggle();
          }
        }}
      >
        <svg width={hasFilter ? 14 : 12} height={hasFilter ? 14 : 12} viewBox="0 0 12 12" fill="currentColor">
          <path d="M1 2.5h10L7 6.5v4l-2-1v-3L1 2.5z" />
        </svg>
      </span>
      {/* Portaled to <body>: the dropdown is position:fixed with viewport
          coordinates, but fixed positioning resolves against the nearest
          transformed/backdrop-filtered ancestor — inside a reporting glass
          card (backdrop-filter) it rendered mid-table instead of under the
          header. In <body> the viewport is always the containing block. */}
      {isOpen && createPortal(
        <div
          ref={(el) => {
            (dropdownRef as any).current = el;
            if (el) {
              const th = triggerRef.current?.closest('th');
              if (th) {
                const rect = th.getBoundingClientRect();
                const dropdownWidth = 240;
                const maxHeight = 340; // .filterDropdown max-height
                const margin = 12;
                // Open toward whichever side of the header has room for the
                // full dropdown, capping its height so it never runs off the
                // viewport (the value list scrolls within).
                const spaceBelow = window.innerHeight - rect.bottom - margin;
                const spaceAbove = rect.top - margin;
                if (spaceBelow >= maxHeight || spaceBelow >= spaceAbove) {
                  el.style.top = `${rect.bottom + 4}px`;
                  el.style.bottom = 'auto';
                  el.style.maxHeight = `${Math.min(maxHeight, spaceBelow)}px`;
                } else {
                  el.style.top = 'auto';
                  el.style.bottom = `${window.innerHeight - rect.top + 4}px`;
                  el.style.maxHeight = `${Math.min(maxHeight, spaceAbove)}px`;
                }
                // If dropdown would overflow right edge, align to column's right instead
                if (rect.left + dropdownWidth > window.innerWidth - 16) {
                  el.style.left = 'auto';
                  el.style.right = `${window.innerWidth - rect.right}px`;
                } else {
                  el.style.left = `${rect.left}px`;
                  el.style.right = 'auto';
                }
              }
            }
          }}
          className={styles.filterDropdown}
          onClick={(e) => e.stopPropagation()}
        >
          {isDate ? (
            <DateFilter
              field={field}
              currentDays={currentDays}
              onApply={onApply}
              onClear={onClear}
            />
          ) : (
            <ValueFilter
              field={field}
              selectedValues={selectedValues}
              onApply={onApply}
              onClear={onClear}
            />
          )}
        </div>,
        document.body,
      )}
    </span>
  );
}
