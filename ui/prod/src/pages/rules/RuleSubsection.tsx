// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, type ReactNode } from 'react';
import styles from './RuleSubsection.module.css';

interface RuleSubsectionProps {
  title: string;
  /**
   * What the group holds, already filtered — e.g. "4 rules". Counts the rows
   * actually shown, not the raw group, so it stays true under search and the
   * "show disabled" toggle. Each caller words its own noun: alert groups hold
   * rules, Posture holds categories.
   */
  count?: string;
  defaultExpanded?: boolean;
  children: ReactNode;
}

/**
 * Collapsible sub-section under "AssetAtlas Rules" — one per subtype
 * (Posture, Security, Operational, …). Clicking the header toggles its
 * contents. Defaults to expanded so the page opens fully visible.
 */
export function RuleSubsection({
  title,
  count,
  defaultExpanded = true,
  children,
}: RuleSubsectionProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  return (
    <div className={styles.section}>
      <button
        type="button"
        className={styles.header}
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <svg
          className={`${styles.chev} ${expanded ? styles.chevOpen : ''}`}
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
        <span className={styles.title}>{title}</span>
        {/* Explicit space: the gap between these is flex layout, which the
            accessible-name calculation does not see. Without it a screen
            reader announces "Posture2 categories". */}
        {count && <> <span className={styles.count}>{count}</span></>}
      </button>
      {expanded && <div>{children}</div>}
    </div>
  );
}
