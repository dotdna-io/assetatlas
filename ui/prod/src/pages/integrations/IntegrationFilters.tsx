// SPDX-License-Identifier: AGPL-3.0-or-later
import { Button } from '@/components/ui/Button';
import styles from './IntegrationFilters.module.css';

interface IntegrationFiltersProps {
  searchQuery: string;
  onSearchChange: (query: string) => void;
  onNewIntegration: () => void;
}

export function IntegrationFilters({
  searchQuery,
  onSearchChange,
  onNewIntegration,
}: IntegrationFiltersProps) {
  return (
    <div className={styles.filters}>
      <input
        type="text"
        className={styles.searchInput}
        placeholder="Search integrations..."
        value={searchQuery}
        onChange={(e) => onSearchChange(e.target.value)}
      />
      <Button className={styles.newIntegrationBtn} variant="primary" onClick={onNewIntegration}>New Integration</Button>
    </div>
  );
}

interface ViewToggleProps {
  viewMode: 'cards' | 'table';
  onViewModeChange: (mode: 'cards' | 'table') => void;
}

export function ViewToggle({ viewMode, onViewModeChange }: ViewToggleProps) {
  return (
    <div className={styles.viewToggle}>
      <button
        type="button"
        className={`${styles.viewButton}${viewMode === 'cards' ? ` ${styles.viewButtonActive}` : ''}`}
        onClick={() => onViewModeChange('cards')}
        title="Card view"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
      </button>
      <button
        type="button"
        className={`${styles.viewButton}${viewMode === 'table' ? ` ${styles.viewButtonActive}` : ''}`}
        onClick={() => onViewModeChange('table')}
        title="Table view"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
      </button>
    </div>
  );
}
