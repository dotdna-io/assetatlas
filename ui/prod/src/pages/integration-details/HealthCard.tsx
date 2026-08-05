// SPDX-License-Identifier: AGPL-3.0-or-later
import { useIntegrationHealth } from '@/api/integrations';
import type { Integration } from '@/api/integrations';
import { formatRelativeTime, getRunAge } from '@/pages/integrations/integration-utils';
import styles from './OverviewCards.module.css';

interface HealthCardProps {
  integration: Integration;
  refetchInterval: number;
}

export function HealthCard({ integration, refetchInterval }: HealthCardProps) {
  const { data } = useIntegrationHealth(integration.id, { refetchInterval });
  const health = data;

  const status = health?.status ?? 'unknown';
  const dotClass =
    status === 'healthy' ? styles.healthy :
    status === 'warning' ? styles.warning :
    status === 'unhealthy' ? styles.unhealthy :
    styles.unknown;

  const runAge = getRunAge(health?.last_successful_run);
  const runAgeClass = styles[runAge];

  const connStatus = integration.last_connection_test_status;
  const connectionText =
    connStatus === 'success' ? 'Connected' :
    connStatus === 'error' ? 'Failed' :
    'Not tested';

  return (
    <div className={styles.card}>
      <div className={styles.cardTitle}>Health</div>

      <div className={styles.row}>
        <span className={styles.label}>Overall Health</span>
        <span className={styles.value}>
          <span className={`${styles.statusDot} ${dotClass}`} />
          {status.charAt(0).toUpperCase() + status.slice(1)}
        </span>
      </div>

      <div className={styles.row}>
        <span className={styles.label}>Last Run</span>
        <span className={`${styles.value} ${runAgeClass}`}>
          {formatRelativeTime(health?.last_successful_run)}
        </span>
      </div>

      <div className={styles.row}>
        <span className={styles.label}>Connection</span>
        <span className={styles.value}>{connectionText}</span>
      </div>
    </div>
  );
}
