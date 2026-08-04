// SPDX-License-Identifier: AGPL-3.0-or-later
import type { ReactNode, CSSProperties } from 'react';
import styles from './Badge.module.css';

type BadgeVariant = 'success' | 'danger' | 'warning' | 'info' | 'default';

interface BadgeProps {
  variant?: BadgeVariant;
  color?: string;
  children: ReactNode;
  className?: string;
}

export function Badge({ variant = 'default', color, children, className }: BadgeProps) {
  const classes = [
    styles.badge,
    color ? styles.custom : styles[variant],
    className,
  ].filter(Boolean).join(' ');

  const style: CSSProperties | undefined = color
    ? { backgroundColor: `${color}33`, color }
    : undefined;

  return (
    <span className={classes} style={style}>
      {children}
    </span>
  );
}
