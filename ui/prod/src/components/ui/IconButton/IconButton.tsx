// SPDX-License-Identifier: AGPL-3.0-or-later
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import styles from './IconButton.module.css';

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
}

export function IconButton({ children, className, ...rest }: IconButtonProps) {
  return (
    <button
      type="button"
      className={`${styles.iconButton} ${className || ''}`}
      {...rest}
    >
      {children}
    </button>
  );
}
