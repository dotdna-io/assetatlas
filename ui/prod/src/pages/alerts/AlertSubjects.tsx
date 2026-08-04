// SPDX-License-Identifier: AGPL-3.0-or-later
import type { AlertSubject } from '@/api/alerts';
import styles from './alerts.module.css';

const PANE_TITLE: Record<string, string> = {
  integration: 'Data Sources',
  audit_event: 'Audit Events',
};

export function AlertSubjects({
  subjectType, subjects,
}: { subjectType: string; subjects?: AlertSubject[] | null }) {
  const title = PANE_TITLE[subjectType];
  if (!title || !subjects || subjects.length === 0) return null;
  return (
    <div className={styles.expandedRight}>
      <span className={styles.expandedLabel}>{title}</span>
      <div className={styles.subjectList}>
        {subjects.map((s) => {
          const row = (
            <>
              <span className={styles.subjectLabel}>{s.label}</span>
              {s.sublabel && <span className={styles.subjectSublabel}>{s.sublabel}</span>}
            </>
          );
          return subjectType === 'integration' ? (
            <a key={s.id} href={`/integration-details?id=${encodeURIComponent(s.id)}`}
               target="_blank" rel="noopener noreferrer" className={styles.subjectRow}>{row}</a>
          ) : (
            <div key={s.id} className={styles.subjectRow}>{row}</div>
          );
        })}
      </div>
    </div>
  );
}
