// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { MonthCalendar } from '@/components/ui/TimeRangeGroup/DateRangeDropdown';
import trgStyles from '@/components/ui/TimeRangeGroup/TimeRangeGroup.module.css';

interface ExpiryPreset {
  days: number;
  label: string;
}

interface ApiKeyExpiryPickerProps {
  presets: ReadonlyArray<ExpiryPreset>;
  /** Active preset days, or `null` when a custom date is in use. */
  activePreset: number | null;
  /** ISO yyyy-mm-dd of the currently chosen expiry. */
  expiresAt: string;
  onPresetChange: (days: number) => void;
  onCustomDate: (iso: string) => void;
}

function formatShort(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function todayPlus(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

const DROPDOWN_HEIGHT_ESTIMATE = 320;
const DROPDOWN_MARGIN = 8;

export function ApiKeyExpiryPicker({
  presets,
  activePreset,
  expiresAt,
  onPresetChange,
  onCustomDate,
}: ApiKeyExpiryPickerProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(expiresAt);
  const [pos, setPos] = useState<{ top: number; left: number; placement: 'below' | 'above' }>({
    top: 0,
    left: 0,
    placement: 'below',
  });
  const wrapRef = useRef<HTMLDivElement>(null);
  const customBtnRef = useRef<HTMLButtonElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const isCustom = activePreset === null;

  // Outside-click close: must include the portal root, hence the dropdownRef
  // check (the wrapper alone wouldn't contain it).
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      const target = e.target as Node;
      if (
        wrapRef.current && !wrapRef.current.contains(target) &&
        dropdownRef.current && !dropdownRef.current.contains(target)
      ) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  // Re-position on scroll/resize while open. The portal sits in document.body
  // so it doesn't follow modal scrolling automatically — flip to above when
  // the trigger is too close to the viewport bottom.
  useEffect(() => {
    if (!open) return;
    function compute() {
      const btn = customBtnRef.current;
      if (!btn) return;
      const rect = btn.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const placement = spaceBelow < DROPDOWN_HEIGHT_ESTIMATE && rect.top > spaceBelow ? 'above' : 'below';
      const top = placement === 'below'
        ? rect.bottom + DROPDOWN_MARGIN
        : rect.top - DROPDOWN_MARGIN;
      // Right-align to the Custom button so it doesn't run off-screen on
      // narrow viewports.
      const left = Math.max(8, rect.right - 280);
      setPos({ top, left, placement });
    }
    compute();
    window.addEventListener('scroll', compute, true);
    window.addEventListener('resize', compute);
    return () => {
      window.removeEventListener('scroll', compute, true);
      window.removeEventListener('resize', compute);
    };
  }, [open]);

  const draftDate = draft ? new Date(draft + 'T00:00:00') : new Date();
  const [view, setView] = useState({
    year: draftDate.getFullYear(),
    month: draftDate.getMonth(),
  });

  const navMonth = useCallback((delta: number) => {
    setView((v) => {
      let m = v.month + delta;
      let y = v.year;
      if (m < 0) { m = 11; y--; }
      if (m > 11) { m = 0; y++; }
      return { year: y, month: m };
    });
  }, []);

  const minDate = todayPlus(1);
  const canApply = draft && draft >= minDate;

  return (
    <div className={trgStyles.wrapper} ref={wrapRef}>
      <div className={trgStyles.group}>
        {presets.map((p) => (
          <button
            key={p.days}
            type="button"
            className={`${trgStyles.btn} ${activePreset === p.days ? trgStyles.active : ''}`}
            onClick={() => {
              onPresetChange(p.days);
              setOpen(false);
            }}
          >
            {p.label}
          </button>
        ))}
        <button
          ref={customBtnRef}
          type="button"
          className={`${trgStyles.btn} ${isCustom ? trgStyles.active : ''}`}
          onClick={() => {
            if (!open) {
              setDraft(expiresAt);
              const d = expiresAt ? new Date(expiresAt + 'T00:00:00') : new Date();
              setView({ year: d.getFullYear(), month: d.getMonth() });
            }
            setOpen((v) => !v);
          }}
        >
          {isCustom ? formatShort(expiresAt) : 'Custom'}
        </button>
      </div>

      {open && createPortal(
        <div
          ref={dropdownRef}
          className={trgStyles.dropdown}
          style={{
            position: 'fixed',
            top: pos.placement === 'below' ? pos.top : undefined,
            bottom: pos.placement === 'above' ? window.innerHeight - pos.top : undefined,
            left: pos.left,
            right: 'auto',
            minWidth: 280,
            zIndex: 10001,
          }}
        >
          <MonthCalendar
            year={view.year}
            month={view.month}
            selected={draft}
            rangeStart=""
            rangeEnd=""
            onSelect={(iso) => {
              if (iso >= minDate) setDraft(iso);
            }}
            onNav={navMonth}
          />
          <div className={trgStyles.dropdownFooter}>
            <span className={trgStyles.dropdownRange}>
              {draft ? formatShort(draft) : '---'}
            </span>
            <div className={trgStyles.dropdownActions}>
              <button
                type="button"
                className={trgStyles.dropdownCancel}
                onClick={() => setOpen(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className={trgStyles.dropdownApply}
                disabled={!canApply}
                onClick={() => {
                  if (canApply) {
                    onCustomDate(draft);
                    setOpen(false);
                  }
                }}
              >
                Apply
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
