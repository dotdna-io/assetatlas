// SPDX-License-Identifier: AGPL-3.0-or-later
import { useRecentErrors } from '@/api/integrations';
import { formatRelativeTime } from '@/pages/integrations/integration-utils';
import styles from './RecentErrors.module.css';

interface RecentErrorsProps {
  integrationId: string;
  refetchInterval: number;
}

export function RecentErrors({ integrationId, refetchInterval }: RecentErrorsProps) {
  const { data } = useRecentErrors(integrationId, 10, { refetchInterval });

  if (!data?.errors?.length) return null;

  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>Recent Errors</div>
      {data.errors.map((error, index) => (
        <div key={index} className={styles.errorCard}>
          <div className={styles.timestamp}>{formatRelativeTime(error.timestamp)}</div>
          <div className={styles.message}>{error.message}</div>
        </div>
      ))}
    </div>
  );
}
