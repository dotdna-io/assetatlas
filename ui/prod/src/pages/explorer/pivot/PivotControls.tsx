// SPDX-License-Identifier: AGPL-3.0-or-later
import type { FieldCatalogEntry } from '@/types/field-id';
import type { ExplorerState } from '../explorer-state';
import { getDisplayName } from '../explorer-utils';
import styles from './pivot.module.css';

interface PivotControlsProps {
  config: ExplorerState['pivotConfig'];
  onConfigChange: (config: Partial<ExplorerState['pivotConfig']>) => void;
  fields: FieldCatalogEntry[];
}

// Exclude high-cardinality or unpivotable fields (keys are encoded FieldIds).
const EXCLUDED_KEYS = new Set([
  'static:Hostname',
  'static:IP%20Address',
  'static:MAC%20Address',
  'static:Serial%20Number',
  'static:FQDN',
]);
const EXCLUDED_TYPES = new Set(['string_array', 'timestamp']);

function getPivotableFields(fields: FieldCatalogEntry[]) {
  return fields.filter(
    (f) => !EXCLUDED_TYPES.has(f.data_type) && !EXCLUDED_KEYS.has(f.encoded_key),
  );
}

export function PivotControls({ config, onConfigChange, fields }: PivotControlsProps) {
  const pivotable = getPivotableFields(fields);

  function handleToggleColumns() {
    const next = !config.columnsEnabled;
    onConfigChange({ columnsEnabled: next });
  }

  return (
    <div className={styles.controls}>
      <label className={styles.controlLabel}>Rows:</label>
      <select
        className={styles.controlSelect}
        value={config.rowDimension}
        onChange={(e) => onConfigChange({ rowDimension: e.target.value })}
      >
        {pivotable.map((f) => (
          <option key={f.encoded_key} value={f.encoded_key}>
            {getDisplayName(f)}
          </option>
        ))}
      </select>

      <button
        className={`${styles.toggleBtn} ${config.columnsEnabled ? styles.toggleBtnActive : ''}`}
        onClick={handleToggleColumns}
        type="button"
      >
        Columns
      </button>

      {config.columnsEnabled && (
        <>
          <label className={styles.controlLabel}>Columns:</label>
          <select
            className={styles.controlSelect}
            value={config.columnDimension}
            onChange={(e) => onConfigChange({ columnDimension: e.target.value })}
          >
            {pivotable.map((f) => (
              <option key={f.encoded_key} value={f.encoded_key}>
                {getDisplayName(f)}
              </option>
            ))}
          </select>
        </>
      )}

      <label className={styles.controlLabel}>Show:</label>
      <select
        className={styles.controlSelect}
        value={config.displayMode}
        onChange={(e) =>
          onConfigChange({
            displayMode: e.target.value as ExplorerState['pivotConfig']['displayMode'],
          })
        }
      >
        <option value="count">Count</option>
        <option value="percent_total">% of total</option>
        <option value="percent_row">% of row</option>
        <option value="percent_col">% of column</option>
      </select>
    </div>
  );
}
