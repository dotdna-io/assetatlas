// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useRef, useState } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import styles from './Button.module.css';

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'success' | 'ghost';
type ButtonSize = 'sm' | 'md' | 'lg';

/** How long the button stays green reading "Success". */
const SUCCESS_FLASH_MS = 1500;

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  /**
   * Success signal: each time this becomes a new non-null value the button
   * turns green and reads the success label for a moment, then reverts.
   * Wire it via successFor(status, 'action') from InlineStatus so only the
   * button that was clicked flashes.
   */
  success?: object | null;
  /** Text shown during the success flash — e.g. "Saved" on save buttons. */
  successLabel?: string;
  children: ReactNode;
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  success = null,
  successLabel = 'Success',
  disabled,
  children,
  className,
  style,
  type = 'button',
  ...rest
}: ButtonProps) {
  const [flashing, setFlashing] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);
  // Freeze the width while "Success" replaces the label so the button
  // doesn't jump narrower or wider mid-flash.
  const [frozenWidth, setFrozenWidth] = useState<number | null>(null);

  // Object identity (not deep equality) drives the restart, so firing the
  // same action twice replays the flash — same contract as SuccessFlash.
  useEffect(() => {
    if (!success) return;
    setFrozenWidth(ref.current ? ref.current.offsetWidth : null);
    setFlashing(true);
    const t = setTimeout(() => {
      setFlashing(false);
      setFrozenWidth(null);
    }, SUCCESS_FLASH_MS);
    return () => clearTimeout(t);
  }, [success]);

  const classes = [
    styles.btn,
    styles[variant],
    styles[size],
    flashing && styles.flashSuccess,
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <button
      ref={ref}
      type={type}
      className={classes}
      style={frozenWidth != null ? { ...style, minWidth: frozenWidth } : style}
      disabled={disabled || loading}
      aria-disabled={disabled || loading || undefined}
      {...rest}
    >
      {flashing ? (
        <>
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <polyline points="20 6 9 17 4 12" />
          </svg>
          {successLabel}
        </>
      ) : (
        <>
          {loading && <span className={styles.spinner} aria-hidden="true" />}
          {children}
        </>
      )}
    </button>
  );
}
