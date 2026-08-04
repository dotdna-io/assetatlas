// SPDX-License-Identifier: AGPL-3.0-or-later
import type { ReactNode } from 'react';
import styles from './SettingsFullWidthBlock.module.css';

interface SettingsFullWidthBlockProps {
  children: ReactNode;
}

export function SettingsFullWidthBlock({ children }: SettingsFullWidthBlockProps) {
  return <div className={styles.block}>{children}</div>;
}
