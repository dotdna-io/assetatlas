// SPDX-License-Identifier: AGPL-3.0-or-later
import type { ReactNode } from 'react';
import styles from './SettingsSection.module.css';

interface SettingsSectionProps {
  // ReactNode, not string: a descriptive preamble sometimes needs to break
  // across lines. Every existing caller passes a plain string, which this
  // still accepts.
  title: ReactNode;
  subtitle?: string;
  children: ReactNode;
  // When true, the header renders the title as a regular-case descriptive
  // sentence instead of the small uppercase chip — used by tabs whose
  // "title" is actually a one-line preamble (e.g. LDAP/OIDC describing what
  // the section configures).
  descriptive?: boolean;
}

export function SettingsSection({
  title, subtitle, children, descriptive,
}: SettingsSectionProps) {
  return (
    <section className={styles.section}>
      <header className={`${styles.header} ${descriptive ? styles.headerDescriptive : ''}`}>
        <span className={`${styles.title} ${descriptive ? styles.titleDescriptive : ''}`}>{title}</span>
        {subtitle && <span className={styles.subtitle}>{subtitle}</span>}
      </header>
      <div className={styles.body}>{children}</div>
    </section>
  );
}
