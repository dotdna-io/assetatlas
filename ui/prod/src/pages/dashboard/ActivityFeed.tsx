// SPDX-License-Identifier: AGPL-3.0-or-later
import { assetUrl } from '@/basePath';
import { timeAgo } from '@/utils/formatters';
import styles from './dashboard.module.css';

interface AlertItem {
  title: string;
  severity?: string;
  created_at?: string;
}

interface ActivityFeedProps {
  alerts: AlertItem[];
}

function getSeverityColor(sev: string): string {
  switch (sev) {
    case 'critical': return 'var(--error)';
    case 'high': return 'var(--warning)';
    case 'medium': return '#3b82f6';
    case 'low': return 'var(--text-tertiary)';
    default: return 'var(--text-tertiary)';
  }
}

function openAlerts() {
  window.open(assetUrl('/alerts'), '_blank');
}

export function ActivityFeed({ alerts }: ActivityFeedProps) {
  const items = alerts.slice(0, 5);

  return (
    <div className={`${styles.intelCard} ${styles.alertsCard}`}>
      <div className={styles.alertsTitle}>Latest Alerts</div>

      <div className={styles.alertsList}>
        {items.length === 0 ? (
          <div className={styles.alertsEmpty}>No alerts</div>
        ) : (
          items.map((a, i) => {
            const sev = (a.severity || 'medium').toLowerCase();
            const ago = a.created_at ? timeAgo(a.created_at) : '';

            return (
              <div className={styles.alertRow} key={i}>
                <div
                  className={styles.alertRowBorder}
                  style={{ backgroundColor: getSeverityColor(sev) }}
                />
                <span
                  className={styles.alertRowName}
                  onClick={openAlerts}
                  role="link"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === 'Enter') openAlerts(); }}
                >
                  {a.title}
                </span>
                <span className={styles.alertRowTime}>{ago}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

export function ActivityFeedSkeleton() {
  return (
    <div className={`${styles.intelCard} ${styles.alertsCard}`} aria-busy="true">
      <div className={styles.alertsTitle}>Latest Alerts</div>
      <div className={styles.alertsList}>
        {Array.from({ length: 5 }).map((_, i) => (
          <div className={styles.skelAlertRow} key={i}>
            <span className={`${styles.skeleton} ${styles.skelAlertBorder}`} />
            <span className={`${styles.skeleton} ${styles.skelAlertTitle}`} />
            <span className={`${styles.skeleton} ${styles.skelAlertTime}`} />
          </div>
        ))}
      </div>
    </div>
  );
}
