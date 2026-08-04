// SPDX-License-Identifier: AGPL-3.0-or-later
import type { ButtonHTMLAttributes } from 'react';
import { IconButton } from './IconButton';
import styles from './DeleteIconButton.module.css';

interface DeleteIconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Icon size in px. Default matches the app-standard 14px action icons. */
  size?: number;
}

/**
 * The one trash button: canonical glyph (alert channels' trash) that turns
 * red on hover. Every delete/remove icon in the app renders through this —
 * don't inline trash SVGs or add per-page hover colours.
 */
export function DeleteIconButton({ size = 14, className, ...rest }: DeleteIconButtonProps) {
  return (
    <IconButton className={`${styles.delete} ${className || ''}`} {...rest}>
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="3 6 5 6 21 6" />
        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      </svg>
    </IconButton>
  );
}
