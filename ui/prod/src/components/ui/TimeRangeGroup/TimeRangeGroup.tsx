// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useRef, useEffect } from 'react';
import { DateRangeDropdown } from './DateRangeDropdown';
import styles from './TimeRangeGroup.module.css';

const DEFAULT_OPTIONS = [
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
];

interface TimeRangeGroupProps {
  value: string;
  onChange: (value: string) => void;
  options?: { value: string; label: string }[];
  customRange?: { start: string; end: string } | null;
  onCustomRange?: (start: string, end: string) => void;
}

function formatRangeLabel(start: string, end: string): string {
  const fmt = (iso: string) => {
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  };
  return `${fmt(start)} - ${fmt(end)}`;
}

export function TimeRangeGroup({
  value,
  onChange,
  options = DEFAULT_OPTIONS,
  customRange,
  onCustomRange,
}: TimeRangeGroupProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  const isCustomActive = value === 'custom' && customRange != null;

  return (
    <div className={styles.wrapper} ref={wrapRef}>
      <div className={styles.group}>
        {options.map((opt) => (
          <button
            key={opt.value}
            className={`${styles.btn} ${value === opt.value && !isCustomActive ? styles.active : ''}`}
            onClick={() => {
              onChange(opt.value);
              setOpen(false);
            }}
          >
            {opt.label}
          </button>
        ))}
        {onCustomRange && (
          <button
            className={`${styles.btn} ${isCustomActive ? styles.active : ''}`}
            onClick={() => setOpen((v) => !v)}
          >
            {isCustomActive ? formatRangeLabel(customRange!.start, customRange!.end) : 'Custom'}
          </button>
        )}
      </div>

      {open && onCustomRange && (
        <DateRangeDropdown
          initialStart={customRange?.start}
          initialEnd={customRange?.end}
          onApply={(start, end) => {
            onCustomRange(start, end);
            onChange('custom');
            setOpen(false);
          }}
          onClose={() => {
            setOpen(false);
            if (isCustomActive) {
              onChange('7d');
            }
          }}
        />
      )}
    </div>
  );
}
