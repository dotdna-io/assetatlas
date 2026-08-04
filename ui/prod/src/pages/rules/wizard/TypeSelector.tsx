// SPDX-License-Identifier: AGPL-3.0-or-later
import styles from '../rules.module.css';

export type RuleType = 'compliance' | 'classification' | 'alert';

interface TypeSelectorProps {
  onSelect: (type: RuleType) => void;
}

export function TypeSelector({ onSelect }: TypeSelectorProps) {
  return (
    <div className={styles.typeCards}>
      <div
        className={styles.typeCard}
        onClick={() => onSelect('compliance')}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && onSelect('compliance')}
      >
        <img src="/compliance.png" alt="" className={styles.typeImg} />
        <div className={styles.typeTitle}>Compliance Rule</div>
        <div className={styles.typeDesc}>
          Evaluate assets against criteria and tag them as passing or failing.
        </div>
      </div>

      <div
        className={styles.typeCard}
        onClick={() => onSelect('classification')}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && onSelect('classification')}
      >
        <svg className={styles.typeIcon} viewBox="0 0 24 24" fill="none" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" stroke="#6b7280" />
          <circle cx="7" cy="7" r="1.5" fill="#10b981" stroke="#10b981" />
        </svg>
        <div className={styles.typeTitle}>Classification Rule</div>
        <div className={styles.typeDesc}>
          Categorize assets with a label and color based on filter criteria.
        </div>
      </div>

      <div
        className={styles.typeCard}
        onClick={() => onSelect('alert')}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && onSelect('alert')}
      >
        <svg className={styles.typeIcon} viewBox="0 0 24 24" fill="none" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" stroke="#ef4444" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" stroke="#ef4444" />
        </svg>
        <div className={styles.typeTitle}>Alert Rule</div>
        <div className={styles.typeDesc}>
          Monitor thresholds and trigger alerts when conditions are met.
        </div>
      </div>
    </div>
  );
}
