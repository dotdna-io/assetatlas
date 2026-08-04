// SPDX-License-Identifier: AGPL-3.0-or-later
import { ChangeEvent } from 'react';
import { PRESETS } from './timeRange';
import type { CustomRange, RangePreset } from './types';
import styles from './TimeRangePicker.module.css';

interface TimeRangePickerProps {
  preset: RangePreset;
  custom?: CustomRange;
  onChange: (preset: RangePreset, custom?: CustomRange) => void;
}

function unixToInput(seconds: number): string {
  return new Date(seconds * 1000).toISOString().slice(0, 16);
}

function inputToUnix(value: string): number {
  return Math.floor(new Date(value).getTime() / 1000);
}

export function TimeRangePicker({ preset, custom, onChange }: TimeRangePickerProps) {
  const handleStartChange = (e: ChangeEvent<HTMLInputElement>) => {
    const start = inputToUnix(e.target.value);
    const end = custom?.end ?? Math.floor(Date.now() / 1000);
    onChange('custom', { start, end });
  };
  const handleEndChange = (e: ChangeEvent<HTMLInputElement>) => {
    const end = inputToUnix(e.target.value);
    const start = custom?.start ?? Math.floor(Date.now() / 1000) - 3600;
    onChange('custom', { start, end });
  };

  return (
    <div className={styles.wrap}>
      {PRESETS.map((p) => (
        <button
          key={p.id}
          type="button"
          className={`${styles.preset} ${preset === p.id ? styles.presetActive : ''}`}
          onClick={() => onChange(p.id)}
        >
          {p.label}
        </button>
      ))}
      {preset === 'custom' && (
        <div className={styles.customWrap}>
          <input
            type="datetime-local"
            className={styles.dateInput}
            value={custom ? unixToInput(custom.start) : ''}
            onChange={handleStartChange}
          />
          <span>→</span>
          <input
            type="datetime-local"
            className={styles.dateInput}
            value={custom ? unixToInput(custom.end) : ''}
            onChange={handleEndChange}
          />
        </div>
      )}
    </div>
  );
}
