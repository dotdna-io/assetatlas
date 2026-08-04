// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useMemo, useEffect } from 'react';
import { useAvailableConnectors } from '@/api/integrations';
import type { Connector } from '@/api/integrations';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { SourceMethodPanel } from './SourceMethodPanel';
import type { WizardMode } from './wizard-state';
import styles from './TypeSelector.module.css';

// Self-contained connectors that ingest data directly without a SIEM/data-lake
// transport step. Clicking these tiles skips SourceMethodPanel entirely.
const CUSTOM_INTEGRATION_TYPES = new Set(['manual_upload', 'custom_http']);

interface TypeSelectorProps {
  onSelectType: (type: string, sourceMethod?: string) => void;
  selectedType: string | null;
  mode?: WizardMode;
  initialSourceMethod?: string;
}

export function TypeSelector({ onSelectType, selectedType, mode, initialSourceMethod }: TypeSelectorProps) {
  const { data, isLoading } = useAvailableConnectors();
  const [search, setSearch] = useState('');
  const [pendingTech, setPendingTech] = useState<Connector | null>(null);

  const allConnectors = data?.connectors ?? [];

  const transports = useMemo(
    () => allConnectors.filter(c => c.connector_role === 'transport'),
    [allConnectors],
  );

  // In edit mode, pre-open the SourceMethodPanel for the already-selected technology connector
  useEffect(() => {
    if (mode === 'edit' && selectedType && allConnectors.length > 0) {
      const tech = allConnectors.find(c => c.integration_type === selectedType);
      if (tech && tech.connector_role === 'technology'
          && !CUSTOM_INTEGRATION_TYPES.has(tech.integration_type)) {
        setPendingTech(tech);
      }
    }
  }, [mode, selectedType, allConnectors]);

  const filtered = useMemo(() => {
    if (!search.trim()) return allConnectors;
    const q = search.toLowerCase();
    return allConnectors.filter(c =>
      c.display_name.toLowerCase().includes(q) ||
      c.integration_type.toLowerCase().includes(q) ||
      (c.description || '').toLowerCase().includes(q) ||
      (c.category || '').toLowerCase().includes(q)
    );
  }, [allConnectors, search]);

  const sections = useMemo(() => {
    const custom: Connector[] = [];
    const siem: Connector[] = [];
    const vendorDirect: Connector[] = [];
    const vendorViaSiem: Connector[] = [];
    for (const c of filtered) {
      if (CUSTOM_INTEGRATION_TYPES.has(c.integration_type)) custom.push(c);
      else if (c.connector_role === 'transport') siem.push(c);
      else if (c.direct_connector) vendorDirect.push(c);
      else vendorViaSiem.push(c);
    }
    return [
      { key: 'siem',           label: 'SIEM / Datalake',                 items: siem },
      { key: 'vendor-direct',  label: 'Vendor (with direct connection)', items: vendorDirect },
      { key: 'vendor-via',     label: 'Vendor (without direct connection)', items: vendorViaSiem },
      { key: 'custom',         label: 'Custom',                          items: custom },
    ];
  }, [filtered]);

  if (isLoading) {
    return <LoadingSpinner />;
  }

  if (pendingTech) {
    return (
      <SourceMethodPanel
        selectedConnector={pendingTech}
        transports={transports}
        initialSourceMethod={initialSourceMethod}
        onConfirm={(sourceMethod) => {
          onSelectType(pendingTech.integration_type, sourceMethod);
          setPendingTech(null);
        }}
        onBack={() => setPendingTech(null)}
      />
    );
  }

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeaderRow}>
        <span className={styles.panelHeader}>Available Integrations ({filtered.length})</span>
        <input
          type="text"
          className={styles.searchInput}
          placeholder="Search connectors..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          autoFocus
        />
      </div>
      {sections.map(section => section.items.length === 0 ? null : (
        <div key={section.key} className={styles.section}>
          <div className={styles.sectionHeader}>
            {section.label} ({section.items.length})
          </div>
          <div className={styles.grid}>
            {section.items.map((connector) => (
              <button
                key={connector.integration_type}
                className={`${styles.typeCard} ${selectedType === connector.integration_type ? styles.typeCardSelected : ''}`}
                onClick={() => {
                  if (connector.connector_role === 'transport') {
                    onSelectType(connector.integration_type);
                  } else if (CUSTOM_INTEGRATION_TYPES.has(connector.integration_type)) {
                    onSelectType(connector.integration_type, 'direct');
                  } else {
                    setPendingTech(connector);
                  }
                }}
                type="button"
              >
                {connector.logo_url && (
                  <img
                    src={connector.logo_url}
                    alt={connector.display_name}
                    className={styles.typeLogo}
                  />
                )}
                <div className={styles.typeName}>{connector.display_name}</div>
                <div className={styles.typeDescription}>{connector.description}</div>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
