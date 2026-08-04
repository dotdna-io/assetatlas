// SPDX-License-Identifier: AGPL-3.0-or-later
import type { AlertRuleThreshold } from '@/api/alerts';
import styles from '../rules.module.css';

interface ThresholdEditorProps {
  value: AlertRuleThreshold | null;
  onChange: (t: AlertRuleThreshold | null) => void;
}

export function ThresholdEditor({ value, onChange }: ThresholdEditorProps) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
      <select
        className={styles.formInput}
        value={value?.condition || ''}
        onChange={(e) => {
          if (e.target.value) {
            onChange({
              condition: e.target.value as AlertRuleThreshold['condition'],
              value: value?.value ?? 80,
              unit: value?.unit || 'percent',
            });
          } else {
            onChange(null);
          }
        }}
      >
        <option value="">No threshold</option>
        <option value="falls_below">Falls below</option>
        <option value="exceeds">Exceeds</option>
      </select>
      {value && (
        <>
          <input
            className={styles.formInput}
            type="number"
            min={0}
            value={value.value}
            onChange={(e) => onChange({ ...value, value: Number(e.target.value) })}
            style={{ width: 80 }}
          />
          <div style={{ display: 'flex', gap: 0 }}>
            <button
              type="button"
              className={styles.formInput}
              style={{
                width: 36, cursor: 'pointer', textAlign: 'center', padding: 0,
                borderRadius: '6px 0 0 6px',
                background: value.unit === 'percent' ? 'var(--accent)' : undefined,
                color: value.unit === 'percent' ? 'white' : undefined,
                borderColor: value.unit === 'percent' ? 'var(--accent)' : undefined,
              }}
              onClick={() => onChange({ ...value, unit: 'percent' })}
            >
              %
            </button>
            <button
              type="button"
              className={styles.formInput}
              style={{
                width: 36, cursor: 'pointer', textAlign: 'center', padding: 0,
                borderRadius: '0 6px 6px 0', borderLeft: 'none',
                background: value.unit === 'number' ? 'var(--accent)' : undefined,
                color: value.unit === 'number' ? 'white' : undefined,
                borderColor: value.unit === 'number' ? 'var(--accent)' : undefined,
              }}
              onClick={() => onChange({ ...value, unit: 'number' })}
            >
              #
            </button>
          </div>
        </>
      )}
    </div>
  );
}
