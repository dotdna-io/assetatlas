// SPDX-License-Identifier: AGPL-3.0-or-later
import type { ReactNode } from 'react';
import styles from './SettingsRow.module.css';

interface SettingsRowProps {
  name: string;
  description?: string;
  meta?: string;
  control: ReactNode;
  onReset?: () => void;
  resetLabel?: string;
  // When true, hide the inline description and surface it as a native
  // browser tooltip on the name. Use for tabs where descriptions are long
  // enough that inline rendering crowds the row (e.g. System -> Security).
  descriptionAsTooltip?: boolean;
  // Span both columns in a 2-column SettingsRowGrid. Use for rows whose value
  // is too long for a half-width cell (e.g. About -> Platform).
  fullWidth?: boolean;
}

export function SettingsRow({
  name,
  description,
  meta,
  control,
  onReset,
  resetLabel,
  descriptionAsTooltip = false,
  fullWidth = false,
}: SettingsRowProps) {
  const showInlineDescription = description && !descriptionAsTooltip;
  const showTooltip = descriptionAsTooltip && description;

  return (
    <div className={`${styles.row} ${fullWidth ? styles.rowFull : ''}`}>
      <div className={styles.info}>
        {showTooltip ? (
          <span className={styles.tooltipWrapper}>
            <span
              className={styles.nameWithTooltip}
              tabIndex={0}
              aria-describedby={`${name}-tooltip`}
            >
              {name}
            </span>
            <span
              role="tooltip"
              id={`${name}-tooltip`}
              className={styles.tooltip}
            >
              {description}
            </span>
          </span>
        ) : (
          <div className={styles.name}>{name}</div>
        )}
        {showInlineDescription && (
          <div className={styles.description}>{description}</div>
        )}
        {meta && <div className={styles.meta}>{meta}</div>}
      </div>
      <div className={styles.controlGroup}>
        {control}
        {onReset && (
          <button
            type="button"
            className={styles.reset}
            onClick={onReset}
            aria-label={resetLabel ?? 'Reset to default'}
          >
            Reset
          </button>
        )}
      </div>
    </div>
  );
}
