// SPDX-License-Identifier: AGPL-3.0-or-later
import { Button } from '@/components/ui/Button';
import type { Integration } from '@/api/integrations';
import { isSensitiveField } from '@/pages/integrations/integration-utils';
import styles from './OverviewCards.module.css';

interface ConnectionCardProps {
  integration: Integration;
  onTestConnection: () => void;
  testStatus: 'idle' | 'testing' | 'success' | 'failed';
}

function formatConfigKey(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function ConnectionCard({ integration, onTestConnection, testStatus }: ConnectionCardProps) {
  const visibleEntries = Object.entries(integration.config).filter(
    ([key]) => !isSensitiveField(key),
  );

  return (
    <div className={styles.card}>
      <div className={styles.cardTitle}>Connection Info</div>

      <div className={styles.scrollList}>
        {visibleEntries.map(([key, value]) => (
          <div key={key} className={styles.row}>
            <span className={styles.configKey}>{formatConfigKey(key)}</span>
            <span className={styles.configValue}>{String(value)}</span>
          </div>
        ))}
      </div>

      <div className={styles.cardFooter}>
        <Button variant="secondary" size="sm" onClick={onTestConnection} loading={testStatus === 'testing'}>
          Test Connection
        </Button>
        {testStatus === 'success' && (
          <span className={styles.testSuccess}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6L9 17l-5-5"/></svg> Connected
          </span>
        )}
        {testStatus === 'failed' && (
          <span className={styles.testFailed}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg> Connection failed
          </span>
        )}
      </div>
    </div>
  );
}
