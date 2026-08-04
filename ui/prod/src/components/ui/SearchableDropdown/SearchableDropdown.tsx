// SPDX-License-Identifier: AGPL-3.0-or-later
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';

import styles from './SearchableDropdown.module.css';

export interface DropdownOption {
  value: string;
  label: string;
  /** Optional group header. Options sharing a group render under the
   *  same heading; options without one render before the first group. */
  group?: string;
}

interface SearchableDropdownProps {
  value: string;
  options: DropdownOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  className?: string;
  disabled?: boolean;
  ariaLabel?: string;
  /** Optional explicit ordering for group headers. Groups not listed
   *  here keep their first-appearance order from `options` and render
   *  after the explicitly-ordered ones. */
  groupOrder?: string[];
}

interface PopoverPos {
  left: number;
  minWidth: number;
  /** Exactly one of `top` or `bottom` is set, depending on placement.
   *  When the popover opens below the trigger, we anchor `top` to the
   *  trigger's bottom edge. When flipped above (because there isn't
   *  enough room below), we anchor `bottom` to the trigger's top edge. */
  top?: number;
  bottom?: number;
  /** Inline cap so the popover can't overflow the viewport on either
   *  side. Falls back to the CSS max-height when there's plenty of
   *  room. */
  maxHeight: number;
}

/** Group filtered options into [groupName, items][] pairs, honouring
 *  `groupOrder` for the explicitly-ordered prefix and falling back to
 *  first-appearance order for the rest. */
function groupOptions(
  options: DropdownOption[],
  groupOrder: string[] | undefined,
): Array<[string, DropdownOption[]]> {
  const buckets = new Map<string, DropdownOption[]>();
  for (const opt of options) {
    const key = opt.group ?? '';
    const list = buckets.get(key) ?? [];
    list.push(opt);
    buckets.set(key, list);
  }
  const ordered: Array<[string, DropdownOption[]]> = [];
  if (groupOrder) {
    for (const k of groupOrder) {
      const items = buckets.get(k);
      if (items) {
        ordered.push([k, items]);
        buckets.delete(k);
      }
    }
  }
  for (const [k, items] of buckets) {
    ordered.push([k, items]);
  }
  return ordered;
}

export function SearchableDropdown({
  value,
  options,
  onChange,
  placeholder = 'Select…',
  searchPlaceholder = 'Search…',
  className,
  disabled,
  ariaLabel,
  groupOrder,
}: SearchableDropdownProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [pos, setPos] = useState<PopoverPos | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const selected = useMemo(
    () => options.find((o) => o.value === value),
    [options, value],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        (o.group ?? '').toLowerCase().includes(q),
    );
  }, [options, query]);

  const grouped = useMemo(
    () => groupOptions(filtered, groupOrder),
    [filtered, groupOrder],
  );

  const updatePos = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const vh = window.innerHeight;
    const gap = 4;
    const edgePad = 8;
    const preferredMax = 406; // matches CSS max-height
    const minHeight = 160;

    const spaceBelow = vh - r.bottom - gap - edgePad;
    const spaceAbove = r.top - gap - edgePad;

    // Prefer opening below; flip above only when below can't fit the
    // preferred height AND above gives us more room. Either way, the
    // popover height is clamped to the available space so it never
    // overflows the viewport.
    const flipAbove = spaceBelow < preferredMax && spaceAbove > spaceBelow;
    const space = flipAbove ? spaceAbove : spaceBelow;
    const maxHeight = Math.max(minHeight, Math.min(preferredMax, space));

    setPos(
      flipAbove
        ? {
            left: r.left,
            minWidth: r.width,
            bottom: vh - r.top + gap,
            maxHeight,
          }
        : {
            left: r.left,
            minWidth: r.width,
            top: r.bottom + gap,
            maxHeight,
          },
    );
  }, []);

  // Reset query whenever the popover closes; recompute position +
  // focus the search input on open.
  useEffect(() => {
    if (!open) {
      setQuery('');
      return;
    }
    updatePos();
    const t = window.setTimeout(() => searchRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, [open, updatePos]);

  // While open: close on outside click / Escape, reposition on
  // scroll / resize so the popover stays anchored to the trigger.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      const t = e.target as Node;
      if (
        !triggerRef.current?.contains(t) &&
        !popoverRef.current?.contains(t)
      ) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    window.addEventListener('scroll', updatePos, true);
    window.addEventListener('resize', updatePos);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', updatePos, true);
      window.removeEventListener('resize', updatePos);
    };
  }, [open, updatePos]);

  const handleSelect = (opt: DropdownOption) => {
    onChange(opt.value);
    setOpen(false);
    triggerRef.current?.focus();
  };

  const triggerClasses = [
    styles.trigger,
    open ? styles.open : '',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={triggerClasses}
        disabled={disabled}
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => !disabled && setOpen((o) => !o)}
      >
        <span
          className={`${styles.triggerLabel} ${selected ? '' : styles.placeholder}`}
        >
          {selected?.label ?? placeholder}
        </span>
        <svg
          className={styles.chevron}
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && pos &&
        createPortal(
          <div
            ref={popoverRef}
            className={styles.popover}
            style={{
              left: pos.left,
              top: pos.top,
              bottom: pos.bottom,
              minWidth: pos.minWidth,
              maxHeight: pos.maxHeight,
            }}
            role="listbox"
            aria-label={ariaLabel}
          >
            <div className={styles.searchWrap}>
              <svg
                className={styles.searchIcon}
                viewBox="0 0 24 24"
                width="14"
                height="14"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <circle cx="11" cy="11" r="7" />
                <path d="M21 21l-4.35-4.35" />
              </svg>
              <input
                ref={searchRef}
                type="text"
                className={styles.search}
                value={query}
                placeholder={searchPlaceholder}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className={styles.list}>
              {grouped.length === 0 ? (
                <div className={styles.empty}>No results</div>
              ) : (
                grouped.map(([groupName, items]) => (
                  <div key={groupName || '__ungrouped__'}>
                    {groupName && (
                      <div className={styles.groupHeader}>{groupName}</div>
                    )}
                    {items.map((opt) => {
                      const isSelected = opt.value === value;
                      const optClasses = [
                        styles.option,
                        isSelected ? styles.optionSelected : '',
                      ]
                        .filter(Boolean)
                        .join(' ');
                      return (
                        <button
                          key={opt.value}
                          type="button"
                          role="option"
                          aria-selected={isSelected}
                          className={optClasses}
                          onClick={() => handleSelect(opt)}
                        >
                          {isSelected ? (
                            <svg
                              className={styles.optionCheck}
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2.5"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              aria-hidden="true"
                            >
                              <polyline points="20 6 9 17 4 12" />
                            </svg>
                          ) : (
                            <span style={{ width: 14, flexShrink: 0 }} />
                          )}
                          <span className={styles.optionLabel}>{opt.label}</span>
                        </button>
                      );
                    })}
                  </div>
                ))
              )}
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
