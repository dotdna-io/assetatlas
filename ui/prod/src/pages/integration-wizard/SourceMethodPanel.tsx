// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import type { Connector } from '@/api/integrations';
import { Button } from '@/components/ui/Button';
import styles from './SourceMethodPanel.module.css';

interface SourceMethodPanelProps {
  selectedConnector: Connector;
  transports: Connector[];
  initialSourceMethod?: string;
  onConfirm: (sourceMethod: string) => void;
  onBack: () => void;
}

export function SourceMethodPanel({
  selectedConnector,
  transports,
  initialSourceMethod,
  onConfirm,
  onBack,
}: SourceMethodPanelProps) {
  const directAvailable = Boolean(selectedConnector.direct_connector);

  const [mode, setMode] = useState<'direct' | 'siem'>(() => {
    if (!directAvailable) return 'siem';
    return initialSourceMethod && initialSourceMethod !== 'direct' ? 'siem' : 'direct';
  });
  const [selectedTransport, setSelectedTransport] = useState<string | null>(
    initialSourceMethod && initialSourceMethod !== 'direct' ? initialSourceMethod : null
  );

  function handleContinue() {
    if (mode === 'direct') {
      onConfirm('direct');
    } else if (selectedTransport) {
      onConfirm(selectedTransport);
    }
  }

  return (
    <div className={styles.panel}>
      <div className={styles.heading}>How do you want to connect?</div>

      {directAvailable && (
        <div className={styles.methodGrid}>
          <button
            type="button"
            className={`${styles.methodCard} ${mode === 'direct' ? styles.methodCardActive : ''}`}
            onClick={() => { setMode('direct'); setSelectedTransport(null); }}
          >
            {selectedConnector.logo_url && (
              <img src={selectedConnector.logo_url} alt="" className={styles.methodLogo} />
            )}
            <div className={styles.methodTitle}>Direct</div>
            <div className={styles.methodDesc}>
              Connect using {selectedConnector.display_name} API credentials
            </div>
          </button>

          <button
            type="button"
            className={`${styles.methodCard} ${mode === 'siem' ? styles.methodCardActive : ''}`}
            onClick={() => setMode('siem')}
          >
            {selectedConnector.logo_url && (
              <img src={selectedConnector.logo_url} alt="" className={styles.methodCornerLogo} />
            )}
            <div className={styles.methodTitle}>Via SIEM / Data Lake</div>
            <div className={styles.methodDesc}>
              Pull {selectedConnector.display_name} data from a SIEM or data lake
            </div>
            <div className={styles.transportLogos}>
              {transports.map((t) => (
                <img key={t.integration_type} src={t.logo_url || ''} alt={t.display_name} className={styles.transportLogoSmall} title={t.display_name} />
              ))}
            </div>
          </button>
        </div>
      )}

      {mode === 'siem' && (
        <>
          <div className={styles.sectionLabel}>
            {directAvailable
              ? 'Select data source'
              : `Pull ${selectedConnector.display_name} data from a SIEM or data lake`}
          </div>
          <div className={styles.transportGrid}>
            {transports.map((t) => (
              <button
                key={t.integration_type}
                type="button"
                className={`${styles.transportCard} ${selectedTransport === t.integration_type ? styles.transportCardActive : ''}`}
                onClick={() => setSelectedTransport(t.integration_type)}
              >
                {t.logo_url && <img src={t.logo_url} alt="" className={styles.transportLogo} />}
                <span>{t.display_name}</span>
              </button>
            ))}
          </div>
        </>
      )}

      <div className={styles.footer}>
        <Button variant="secondary" onClick={onBack}>Cancel</Button>
        <Button
          variant="primary"
          disabled={mode === 'siem' && !selectedTransport}
          onClick={handleContinue}
        >
          Continue
        </Button>
      </div>
    </div>
  );
}
