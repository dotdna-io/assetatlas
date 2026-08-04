// SPDX-License-Identifier: AGPL-3.0-or-later
import styles from './ToggleSwitch.module.css';

interface ToggleSwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  label?: string;
  // Accessible name for the switch. `label` renders visible text *beside*
  // the control without associating it, so a switch whose meaning comes from
  // surrounding markup announces as an unnamed "switch" unless this is set.
  ariaLabel?: string;
}

export function ToggleSwitch({
  checked, onChange, disabled = false, label, ariaLabel,
}: ToggleSwitchProps) {
  return (
    <span className={[styles.wrapper, disabled ? styles.disabled : ''].filter(Boolean).join(' ')}>
      <button
        role="switch"
        aria-checked={checked}
        aria-label={ariaLabel}
        disabled={disabled}
        className={[styles.track, checked ? styles.on : styles.off].join(' ')}
        onClick={() => onChange(!checked)}
        type="button"
      >
        <span className={styles.knob} />
      </button>
      {label && <span className={styles.label}>{label}</span>}
    </span>
  );
}
