// SPDX-License-Identifier: AGPL-3.0-or-later
import { useDocumentTitle } from '@/hooks';
import { useState, useDeferredValue } from 'react';
import { Button } from '@/components/ui/Button';
import { SearchInput } from '@/components/ui/SearchInput';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import { CustomRulesSection } from './rules/CustomRulesSection';
import { AlertRulesSection } from './rules/AlertRulesSection';
import { PostureRulesSection } from './rules/PostureRulesSection';
import { RuleWizard } from './rules/RuleWizard';
import { assetUrl } from '@/basePath';
import styles from './RulesPage.module.css';

const ALERT_ONLY_TYPES = new Set(['security', 'operational', 'data_quality']);

export default function RulesPage() {
  useDocumentTitle('Rules');
  const [isWizardOpen, setIsWizardOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const deferredSearch = useDeferredValue(searchQuery);
  const [severityFilter, setSeverityFilter] = useState<string>('');
  const [typeFilter, setTypeFilter] = useState<string>('');

  const [showDisabled, setShowDisabled] = useState<boolean>(() => {
    try {
      return localStorage.getItem('showDisabledRules') === 'true';
    } catch {
      return false;
    }
  });

  function handleShowDisabledChange(checked: boolean) {
    setShowDisabled(checked);
    try {
      localStorage.setItem('showDisabledRules', String(checked));
    } catch {
      // ignore storage errors
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.actionBar}>
        <SearchInput value={searchQuery} onChange={setSearchQuery} placeholder="Search rules..." width={420} />
        <select
          className={styles.filterSelect}
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value)}
          aria-label="Filter by severity"
        >
          <option value="">All severities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
        <select
          className={styles.filterSelect}
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          aria-label="Filter by type"
        >
          <option value="">All types</option>
          <option value="posture">Posture</option>
          <option value="security">Security</option>
          <option value="compliance">Compliance</option>
          <option value="operational">Operational</option>
          <option value="data_quality">Data Quality</option>
          <option value="classification">Classification</option>
        </select>
        <ToggleSwitch
          checked={showDisabled}
          onChange={handleShowDisabledChange}
          label="Show disabled rules"
        />
        <div className={styles.actionBarSpacer} />
        <Button variant="primary" onClick={() => setIsWizardOpen(true)}>
          Create New Rule
        </Button>
      </div>

      <RuleWizard
        isOpen={isWizardOpen}
        onClose={() => setIsWizardOpen(false)}
        onCreated={() => setIsWizardOpen(false)}
      />

      {!severityFilter && typeFilter !== 'posture' && !ALERT_ONLY_TYPES.has(typeFilter) && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>Custom Rules</div>
          <CustomRulesSection
            searchQuery={deferredSearch}
            showDisabled={showDisabled}
            typeFilter={typeFilter}
          />
        </div>
      )}

      {typeFilter !== 'classification' && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>
            <img src={assetUrl('/assetatlas_logo.png')} className={styles.sectionTitleLogo} alt="" />
            AssetAtlas Rules
          </div>
          {/* Posture rules have no severity/alert-type. Show them when the
              "Posture" type is picked, or when nothing is narrowing the list. */}
          {(typeFilter === 'posture' || (!severityFilter && !typeFilter)) && (
            <PostureRulesSection searchQuery={deferredSearch} />
          )}
          {/* Alert rules are hidden when filtering specifically to Posture. */}
          {typeFilter !== 'posture' && (
            <AlertRulesSection
              searchQuery={deferredSearch}
              showDisabled={showDisabled}
              severityFilter={severityFilter}
              typeFilter={typeFilter}
            />
          )}
        </div>
      )}
    </div>
  );
}
