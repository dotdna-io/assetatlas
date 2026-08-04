// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useCallback } from 'react';
import styles from './TimeRangeGroup.module.css';

const DAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];

interface DateRangeDropdownProps {
  initialStart?: string;
  initialEnd?: string;
  onApply: (start: string, end: string) => void;
  onClose: () => void;
}

function toISO(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function formatDisplay(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function getMonthGrid(year: number, month: number): (number | null)[][] {
  const first = new Date(year, month, 1);
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  // Monday = 0
  let startDay = first.getDay() - 1;
  if (startDay < 0) startDay = 6;

  const weeks: (number | null)[][] = [];
  let week: (number | null)[] = Array(startDay).fill(null);

  for (let d = 1; d <= daysInMonth; d++) {
    week.push(d);
    if (week.length === 7) {
      weeks.push(week);
      week = [];
    }
  }
  if (week.length > 0) {
    while (week.length < 7) week.push(null);
    weeks.push(week);
  }
  return weeks;
}

export function MonthCalendar({
  year,
  month,
  selected,
  rangeStart,
  rangeEnd,
  onSelect,
  onNav,
}: {
  year: number;
  month: number;
  selected: string;
  rangeStart: string;
  rangeEnd: string;
  onSelect: (iso: string) => void;
  onNav: (delta: number) => void;
}) {
  const weeks = getMonthGrid(year, month);
  const monthName = new Date(year, month).toLocaleDateString(undefined, { month: 'long', year: 'numeric' });

  return (
    <div className={styles.calendar}>
      <div className={styles.calNav}>
        <button className={styles.calNavBtn} onClick={() => onNav(-1)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14"><path d="M15 18l-6-6 6-6" /></svg>
        </button>
        <span className={styles.calMonth}>{monthName}</span>
        <button className={styles.calNavBtn} onClick={() => onNav(1)}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14"><path d="M9 18l6-6-6-6" /></svg>
        </button>
      </div>
      <div className={styles.calGrid}>
        {DAYS.map((d) => (
          <span key={d} className={styles.calDayHeader}>{d}</span>
        ))}
        {weeks.flat().map((day, i) => {
          if (day === null) return <span key={i} />;
          const iso = toISO(new Date(year, month, day));
          const isSelected = iso === selected;
          const inRange = rangeStart && rangeEnd && iso >= rangeStart && iso <= rangeEnd;
          return (
            <button
              key={i}
              className={`${styles.calDay} ${isSelected ? styles.calDaySelected : ''} ${inRange && !isSelected ? styles.calDayInRange : ''}`}
              onClick={() => onSelect(iso)}
            >
              {day}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function DateRangeDropdown({ initialStart, initialEnd, onApply, onClose }: DateRangeDropdownProps) {
  const today = toISO(new Date());
  const [start, setStart] = useState(initialStart || '');
  const [end, setEnd] = useState(initialEnd || '');

  const startDate = start ? new Date(start + 'T00:00:00') : new Date();
  const endDate = end ? new Date(end + 'T00:00:00') : new Date();

  const [startView, setStartView] = useState({ year: startDate.getFullYear(), month: startDate.getMonth() });
  const [endView, setEndView] = useState({ year: endDate.getFullYear(), month: endDate.getMonth() });

  const navStart = useCallback((delta: number) => {
    setStartView((v) => {
      let m = v.month + delta;
      let y = v.year;
      if (m < 0) { m = 11; y--; }
      if (m > 11) { m = 0; y++; }
      return { year: y, month: m };
    });
  }, []);

  const navEnd = useCallback((delta: number) => {
    setEndView((v) => {
      let m = v.month + delta;
      let y = v.year;
      if (m < 0) { m = 11; y--; }
      if (m > 11) { m = 0; y++; }
      return { year: y, month: m };
    });
  }, []);

  const canApply = start && end && start <= end;

  return (
    <div className={styles.dropdown}>
      <div className={styles.dropdownCalendars}>
        <div className={styles.dropdownCol}>
          <span className={styles.dropdownLabel}>Start</span>
          <MonthCalendar
            year={startView.year}
            month={startView.month}
            selected={start}
            rangeStart={start}
            rangeEnd={end}
            onSelect={setStart}
            onNav={navStart}
          />
        </div>
        <div className={styles.dropdownCol}>
          <span className={styles.dropdownLabel}>End</span>
          <MonthCalendar
            year={endView.year}
            month={endView.month}
            selected={end}
            rangeStart={start}
            rangeEnd={end}
            onSelect={setEnd}
            onNav={navEnd}
          />
        </div>
      </div>
      <div className={styles.dropdownFooter}>
        <span className={styles.dropdownRange}>
          {start ? formatDisplay(start) : '---'}
          <span className={styles.dropdownRangeSep}> - </span>
          {end ? formatDisplay(end) : '---'}
        </span>
        <div className={styles.dropdownActions}>
          <button className={styles.dropdownCancel} onClick={onClose}>Cancel</button>
          <button
            className={styles.dropdownApply}
            disabled={!canApply}
            onClick={() => canApply && onApply(start, end)}
          >
            Apply
          </button>
        </div>
      </div>
    </div>
  );
}
