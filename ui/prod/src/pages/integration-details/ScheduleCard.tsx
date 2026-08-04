// SPDX-License-Identifier: AGPL-3.0-or-later
import { useIntegrationSchedule } from '@/api/integrations';
import type { Integration } from '@/api/integrations';
import { formatFutureTime } from '@/pages/integrations/integration-utils';
import styles from './OverviewCards.module.css';

interface ScheduleCardProps {
  integration: Integration;
  refetchInterval: number;
}

export function ScheduleCard({ integration, refetchInterval }: ScheduleCardProps) {
  const { data } = useIntegrationSchedule(integration.id, { refetchInterval });
  const schedule = data?.schedule;

  const nextRunText = formatFutureTime(schedule?.next_run_at);

  return (
    <div className={styles.card}>
      <div className={styles.cardTitle}>Schedule</div>

      <div className={styles.row}>
        <span className={styles.label}>Cron</span>
        <span className={styles.value}>
          {schedule?.cron ? <code className={styles.codeChip}>{schedule.cron}</code> : 'None'}
        </span>
      </div>

      <div className={styles.row}>
        <span className={styles.label}>Timezone</span>
        <span className={styles.value}>{schedule?.timezone ?? '—'}</span>
      </div>

      <div className={styles.row}>
        <span className={styles.label}>Status</span>
        <span className={schedule?.enabled ? styles.valueOk : styles.valueError}>
          {schedule?.enabled ? 'Enabled' : 'Disabled'}
        </span>
      </div>

      <div className={styles.row}>
        <span className={styles.label}>Next Run</span>
        <span className={styles.value}>{nextRunText ?? '—'}</span>
      </div>

    </div>
  );
}
