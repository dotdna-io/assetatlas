// SPDX-License-Identifier: AGPL-3.0-or-later
import styles from './ColorPicker.module.css';

const DEFAULT_COLORS = [
  '#ef4444', '#f59e0b', '#eab308', '#22c55e',
  '#10b981', '#14b8a6', '#06b6d4', '#3b82f6',
  '#6366f1', '#8b5cf6', '#ec4899', '#64748b',
];

interface ColorPickerProps {
  value: string;
  onChange: (color: string) => void;
  colors?: string[];
}

export function ColorPicker({ value, onChange, colors = DEFAULT_COLORS }: ColorPickerProps) {
  return (
    <div className={styles.grid} role="radiogroup" aria-label="Color picker">
      {colors.map((color) => {
        const isSelected = color.toLowerCase() === value.toLowerCase();
        return (
          <button
            key={color}
            type="button"
            role="radio"
            aria-checked={isSelected}
            aria-label={color}
            className={[styles.swatch, isSelected ? styles.selected : ''].filter(Boolean).join(' ')}
            style={{ backgroundColor: color }}
            onClick={() => onChange(color)}
          >
            {isSelected && <span className={styles.check} aria-hidden="true">✓</span>}
          </button>
        );
      })}
    </div>
  );
}
