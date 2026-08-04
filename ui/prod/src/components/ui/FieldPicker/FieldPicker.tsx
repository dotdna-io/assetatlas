// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useRef, useMemo, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import type { FieldCatalogEntry } from '@/types/field-id';
import styles from './FieldPicker.module.css';

export interface FieldPickerGroup {
  label: string;
  fields: FieldCatalogEntry[];
}

interface FieldPickerProps {
  /** encoded_key of the selected field, or "" for no selection. */
  value: string;
  onChange: (encoded_key: string) => void;
  groups: FieldPickerGroup[];
  placeholder?: string;
  className?: string;
}

/**
 * Searchable field picker. Replaces native <select><optgroup> when the field
 * catalog is too large for a vanilla dropdown — keeps the panel height bounded
 * (scrollable, max 320px) and filters by substring against display_name.
 */
export function FieldPicker({
  value,
  onChange,
  groups,
  placeholder = 'Select field…',
  className,
}: FieldPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  // Panel rect is computed from the trigger's bounding box so it can be
  // portaled to <body> and escape every clipping ancestor (the wizard
  // and its columns both set overflow: hidden).
  const [panelStyle, setPanelStyle] = useState<React.CSSProperties | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const selectedLabel = useMemo(() => {
    for (const g of groups) {
      const match = g.fields.find((f) => f.encoded_key === value);
      if (match) return match.display_name;
    }
    return '';
  }, [value, groups]);

  const filteredGroups = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return groups;
    return groups
      .map((g) => ({
        label: g.label,
        fields: g.fields.filter((f) => f.display_name.toLowerCase().includes(q)),
      }))
      .filter((g) => g.fields.length > 0);
  }, [query, groups]);

  // Flattened option list — activeIndex indexes into this for keyboard nav.
  const flatOptions = useMemo(
    () => filteredGroups.flatMap((g) => g.fields),
    [filteredGroups],
  );

  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
      const tgt = e.target as Node;
      const insideRoot = rootRef.current?.contains(tgt);
      const insidePanel = panelRef.current?.contains(tgt);
      if (!insideRoot && !insidePanel) setOpen(false);
    }
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [open]);

  // Position the portaled panel against the trigger; reposition on scroll
  // (capture phase — any ancestor scrolling counts) and resize so it stays
  // attached when the wizard animates or the user adjusts the viewport.
  useLayoutEffect(() => {
    if (!open) {
      setPanelStyle(null);
      return;
    }
    function recalc() {
      const el = triggerRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      setPanelStyle({
        position: 'fixed',
        top: r.bottom + 4,
        left: r.left,
        minWidth: r.width,
      });
    }
    recalc();
    window.addEventListener('scroll', recalc, true);
    window.addEventListener('resize', recalc);
    return () => {
      window.removeEventListener('scroll', recalc, true);
      window.removeEventListener('resize', recalc);
    };
  }, [open]);

  useEffect(() => {
    if (open) {
      setQuery('');
      setActiveIndex(0);
      searchRef.current?.focus();
    }
  }, [open]);

  useEffect(() => {
    // Keep activeIndex within bounds whenever the filtered list changes.
    setActiveIndex((i) => Math.max(0, Math.min(i, flatOptions.length - 1)));
  }, [flatOptions.length]);

  function handleSelect(encoded_key: string) {
    onChange(encoded_key);
    setOpen(false);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') {
      setOpen(false);
      e.preventDefault();
    } else if (e.key === 'ArrowDown') {
      setActiveIndex((i) => Math.min(i + 1, Math.max(0, flatOptions.length - 1)));
      e.preventDefault();
    } else if (e.key === 'ArrowUp') {
      setActiveIndex((i) => Math.max(0, i - 1));
      e.preventDefault();
    } else if (e.key === 'Enter') {
      const pick = flatOptions[activeIndex];
      if (pick) handleSelect(pick.encoded_key);
      e.preventDefault();
    }
  }

  const panel =
    open && panelStyle
      ? createPortal(
          <div ref={panelRef} className={styles.panel} role="listbox" style={panelStyle}>
            <input
              ref={searchRef}
              className={styles.search}
              type="text"
              placeholder="Search fields…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
            />
            <div className={styles.list} ref={listRef}>
              {filteredGroups.length === 0 ? (
                <div className={styles.empty}>No fields match</div>
              ) : (
                filteredGroups.map((g) => (
                  <div key={g.label} className={styles.group}>
                    <div className={styles.groupLabel}>{g.label}</div>
                    {g.fields.map((f) => {
                      const flatIdx = flatOptions.indexOf(f);
                      const isActive = flatIdx === activeIndex;
                      const isSelected = f.encoded_key === value;
                      return (
                        <button
                          key={f.encoded_key}
                          type="button"
                          role="option"
                          aria-selected={isSelected}
                          className={[
                            styles.option,
                            isActive ? styles.optionActive : '',
                            isSelected ? styles.optionSelected : '',
                          ].filter(Boolean).join(' ')}
                          onMouseEnter={() => setActiveIndex(flatIdx)}
                          onClick={() => handleSelect(f.encoded_key)}
                        >
                          {f.display_name}
                        </button>
                      );
                    })}
                  </div>
                ))
              )}
            </div>
          </div>,
          document.body,
        )
      : null;

  return (
    <div ref={rootRef} className={`${styles.root} ${className ?? ''}`}>
      <button
        ref={triggerRef}
        type="button"
        className={`${styles.trigger} ${open ? styles.triggerOpen : ''}`}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className={`${styles.triggerLabel} ${selectedLabel ? '' : styles.triggerPlaceholder}`}>
          {selectedLabel || placeholder}
        </span>
        <svg className={styles.chevron} viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {panel}
    </div>
  );
}
