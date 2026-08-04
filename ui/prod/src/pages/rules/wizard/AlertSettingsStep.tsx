// SPDX-License-Identifier: AGPL-3.0-or-later
import { ThresholdEditor } from './ThresholdEditor';
import styles from '../rules.module.css';

export interface AlertSettings {
  severity: string;
  category: 'security' | 'compliance' | 'operational' | 'data_quality';
  threshold: { condition: 'falls_below' | 'exceeds'; value: number; unit: 'percent' | 'number' } | null;
  silence_duration: number | null;
  criteria: { source: string; rule_id?: string } | null;
}

interface AlertSettingsStepProps {
  settings: AlertSettings;
  onChange: (settings: AlertSettings) => void;
}

export function AlertSettingsStep({ settings, onChange }: AlertSettingsStepProps) {
  return (
    <div>
      <div className={styles.formGroup}>
        <label className={styles.formLabel}>Category (required)</label>
        <select
          className={styles.formInput}
          value={settings.category}
          onChange={(e) => onChange({ ...settings, category: e.target.value as AlertSettings['category'] })}
        >
          <option value="security">Security</option>
          <option value="compliance">Compliance</option>
          <option value="operational">Operational</option>
          <option value="data_quality">Data Quality</option>
        </select>
      </div>

      <div className={styles.formGroup}>
        <label className={styles.formLabel}>Severity (required)</label>
        <select
          className={styles.formInput}
          value={settings.severity}
          onChange={(e) => onChange({ ...settings, severity: e.target.value })}
        >
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
      </div>

      <div className={styles.formGroup}>
        <label className={styles.formLabel}>Threshold (optional)</label>
        <p className={styles.formHint}>
          Fires when the number of assets matching this rule — or, in <strong>%</strong> mode,
          their share of the targeted assets — exceeds or falls below this value.
          Leave as “No threshold” to alert on any match.
        </p>
        <ThresholdEditor
          value={settings.threshold}
          onChange={(threshold) => onChange({ ...settings, threshold })}
        />
      </div>

      <div className={styles.formGroup}>
        <label className={styles.formLabel}>Silence Period (optional)</label>
        <select
          className={styles.formInput}
          value={settings.silence_duration ?? ''}
          onChange={(e) => onChange({ ...settings, silence_duration: e.target.value ? Number(e.target.value) : null })}
        >
          <option value="">No silence</option>
          <option value="60">1 hour</option>
          <option value="240">4 hours</option>
          <option value="480">8 hours</option>
          <option value="1440">24 hours</option>
          <option value="4320">3 days</option>
          <option value="10080">7 days</option>
        </select>
      </div>
    </div>
  );
}
