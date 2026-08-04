// SPDX-License-Identifier: AGPL-3.0-or-later
import styles from './LoadingSpinner.module.css';

type SpinnerSize = 'sm' | 'md' | 'lg';

interface LoadingSpinnerProps {
  size?: SpinnerSize;
  className?: string;
}

export function LoadingSpinner({
  size = 'md',
  className,
}: LoadingSpinnerProps) {
  const classes = [styles.spinner, styles[size], className]
    .filter(Boolean)
    .join(' ');

  return <div className={classes} role="status" aria-label="Loading" />;
}
