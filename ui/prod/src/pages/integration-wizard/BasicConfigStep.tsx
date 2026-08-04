// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useRef, useMemo } from 'react';
import { useAvailableConnectors } from '@/api/integrations';
import { useApiQuery } from '@/api/hooks';
import { LogoPickerModal } from './LogoPickerModal';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import type { WizardFormData, WizardMode } from './wizard-state';
import styles from './BasicConfigStep.module.css';

interface BasicConfigStepProps {
  formData: WizardFormData;
  onUpdate: (partial: Partial<WizardFormData>) => void;
  mode: WizardMode;
}

export function BasicConfigStep({ formData, onUpdate, mode }: BasicConfigStepProps) {
  const [logoModalOpen, setLogoModalOpen] = useState(false);
  const [logoError, setLogoError] = useState(false);
  const { data } = useAvailableConnectors();

  const connectors = data?.connectors ?? [];
  const selectedConnector = connectors.find(
    (c) => c.integration_type === formData.integration_type
  );

  // Fetch categories from the source reliability scores setting (single source of truth)
  const { data: scoresData } = useApiQuery<{ success: boolean; value: Record<string, number> }>(
    ['settings', 'source_reliability_scores'],
    '/settings/asset_generation/source_reliability_scores',
    { staleTime: 5 * 60 * 1000 },
  );

  const categories = useMemo(() => {
    const scores = scoresData?.value ?? {};
    return Object.keys(scores).map(key => ({
      value: key,
      label: key.replace(/\b\w/g, c => c.toUpperCase()),
    }));
  }, [scoresData]);

  // Map connector API categories to the settings categories
  const CATEGORY_MAP: Record<string, string> = {
    'identity management': 'identity and access management',
    'edr': 'endpoint security',
    'siem': 'telemetry derived',
    'siem / logging': 'telemetry derived',
    'configuration management': 'cmdb',
    'network discovery': 'asset discovery',
    'network monitoring': 'asset discovery',
    'network infrastructure': 'asset discovery',
    'data import': 'other',
    'data platform': 'other',
  };

  const lastAppliedType = useRef<string | null>(null);

  useEffect(() => {
    if (!selectedConnector) return;
    // Skip until the categories list has loaded — otherwise the auto-apply
    // runs against an empty list, which on the second pass can't re-apply
    // because `lastAppliedType` is already set, leaving categories empty.
    if (categories.length === 0) return;
    if (lastAppliedType.current === selectedConnector.integration_type) return;
    lastAppliedType.current = selectedConnector.integration_type;

    // In edit/clone mode the integration already has name/logo/category —
    // don't overwrite. Custom HTTP exposes the category to the user, so
    // clobbering it from the connector default loses their selection.
    // Drive this off the wizard mode rather than `!formData.name`, which
    // would flip back to "new" if the user temporarily cleared the name.
    const isNew = mode === 'create';
    const updates: Partial<WizardFormData> = {};
    if (isNew) updates.name = selectedConnector.display_name || formData.name;
    if (isNew || !formData.logo) updates.logo = selectedConnector.logo_url || null;

    if (isNew || !formData.category) {
      // Only set category to a value that's actually in the dropdown — keeping
      // an unmappable value (e.g. connector says "Custom" with no matching
      // option) would render the select as blank but pass the truthy check
      // in the wizard's Next-button validation.
      let resolved: string | null = null;
      if (selectedConnector.category) {
        const connCat = selectedConnector.category.toLowerCase();
        const categoryKeys = categories.map(c => c.value);
        if (categoryKeys.includes(connCat)) {
          resolved = connCat;
        } else {
          const mapped = CATEGORY_MAP[connCat];
          if (mapped && categoryKeys.includes(mapped)) {
            resolved = mapped;
          }
        }
      }
      updates.category = resolved;
    }

    onUpdate(updates);
  }, [selectedConnector, categories]);

  return (
    <div className={styles.layout}>
      {/* Main form panel */}
      <div className={styles.panel}>
        <div className={styles.panelHeader}>Integration Details</div>
        <div className={styles.panelBody}>
          <div className={styles.formGrid}>
            {/* Left column: Name + Description + Enabled */}
            <div className={styles.formColumn}>
              <div className={styles.field}>
                <label className={styles.fieldLabel} htmlFor="integration-name">
                  Integration Name <span className={styles.required}>*</span>
                </label>
                <input
                  id="integration-name"
                  type="text"
                  required
                  className={styles.fieldInput}
                  value={formData.name}
                  onChange={(e) => onUpdate({ name: e.target.value })}
                  placeholder="e.g. Production Jamf"
                />
              </div>

              <div className={styles.field}>
                <label className={styles.fieldLabel} htmlFor="integration-description">
                  Description
                </label>
                <textarea
                  id="integration-description"
                  className={styles.fieldTextarea}
                  value={formData.description}
                  onChange={(e) => onUpdate({ description: e.target.value })}
                  placeholder="Optional description..."
                  rows={3}
                />
              </div>

              <div className={styles.toggleRow}>
                <ToggleSwitch
                  checked={formData.enabled}
                  onChange={(checked) => onUpdate({ enabled: checked })}
                />
                <label className={styles.fieldLabel}>Enable this integration</label>
              </div>
            </div>

            {/* Right column: Logo + Category */}
            <div className={styles.formColumn}>
              <div className={styles.field}>
                <label className={styles.fieldLabel}>Logo</label>
                <div className={styles.logoPicker} onClick={() => setLogoModalOpen(true)} role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter') setLogoModalOpen(true); }}>
                  {formData.logo && !logoError ? (
                    <img src={formData.logo} alt="Integration logo" className={styles.logoImage} onError={() => setLogoError(true)} />
                  ) : (
                    <div className={styles.logoEmpty}>
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
                    </div>
                  )}
                  <div className={styles.logoText}>
                    <span className={styles.logoLabel}>{formData.logo ? 'Change logo' : 'Choose a logo'}</span>
                    <span className={styles.logoHint}>Click to browse available logos</span>
                  </div>
                </div>
              </div>

              <div className={styles.field}>
                <label className={styles.fieldLabel}>
                  Data Source Category <span className={styles.required}>*</span>
                </label>
                <select
                  className={styles.fieldInput}
                  value={formData.category || ''}
                  onChange={(e) => onUpdate({ category: e.target.value || null })}
                >
                  <option value="">Select a category...</option>
                  {categories.map(opt => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        </div>
      </div>

      <LogoPickerModal
        isOpen={logoModalOpen}
        onClose={() => setLogoModalOpen(false)}
        onSelect={(path) => { setLogoError(false); onUpdate({ logo: path }); }}
        selectedLogo={formData.logo}
      />
    </div>
  );
}
