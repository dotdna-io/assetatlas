// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useCallback } from 'react';
import { useUpdateSetting } from '@/api/settings';
import { Button } from '@/components/ui/Button';
import { InlineStatus, successFor, useInlineStatus } from '@/components/ui/InlineStatus';
import styles from './ConfidenceThresholds.module.css';

const MIN_GAP = 5; // minimum 0.05 gap between thresholds

interface ConfidenceThresholdsProps {
  settings: Record<string, any> | undefined;
}

export function ConfidenceThresholds({ settings }: ConfidenceThresholdsProps) {
  const [status, setStatus] = useInlineStatus();
  const updateSetting = useUpdateSetting();

  const medSetting = settings?.confidence_threshold_medium;
  const highSetting = settings?.confidence_threshold_high;

  const medDefault = medSetting?.default ?? 0.4;
  const highDefault = highSetting?.default ?? 0.7;
  const medSaved = Math.round((medSetting?.value ?? medDefault) * 100);
  const highSaved = Math.round((highSetting?.value ?? highDefault) * 100);

  const [med, setMed] = useState(medSaved);
  const [high, setHigh] = useState(highSaved);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // Sync from server when settings load
  useEffect(() => {
    setMed(medSaved);
    setHigh(highSaved);
  }, [medSaved, highSaved]);

  const handleMedChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    let m = parseInt(e.target.value, 10);
    if (m > high - MIN_GAP) m = high - MIN_GAP;
    setMed(m);
  }, [high]);

  const handleHighChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    let h = parseInt(e.target.value, 10);
    if (h < med + MIN_GAP) h = med + MIN_GAP;
    setHigh(h);
  }, [med]);

  const handleReset = useCallback(() => {
    setMed(Math.round(medDefault * 100));
    setHigh(Math.round(highDefault * 100));
  }, [medDefault, highDefault]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError('');
    try {
      await Promise.all([
        updateSetting.mutateAsync({
          category: 'asset_generation',
          key: 'confidence_threshold_medium',
          data: { value: med / 100, reason: 'Updated from Settings UI' },
        }),
        updateSetting.mutateAsync({
          category: 'asset_generation',
          key: 'confidence_threshold_high',
          data: { value: high / 100, reason: 'Updated from Settings UI' },
        }),
      ]);
      setStatus({ kind: 'success', message: 'Thresholds saved', action: 'save' });
    } catch (e: any) {
      setError('Failed to save: ' + (e.message || e));
    } finally {
      setSaving(false);
    }
  }, [med, high, updateSetting, setStatus]);

  if (!medSetting || !highSetting) return null;

  return (
    <div className={styles.card}>
      <InlineStatus status={status} />
      <div className={styles.description}>
        Drag the handles to adjust the Low, Medium, and High confidence boundaries.
      </div>

      <div className={styles.sliderWrap}>
        <div className={styles.track}>
          <div className={styles.zoneLow} style={{ flex: med }} />
          <div className={styles.zoneMed} style={{ flex: high - med }} />
          <div className={styles.zoneHigh} style={{ flex: 100 - high }} />
        </div>
        <input
          type="range"
          className={styles.range}
          min={0}
          max={100}
          step={1}
          value={med}
          onChange={handleMedChange}
        />
        <input
          type="range"
          className={styles.range}
          min={0}
          max={100}
          step={1}
          value={high}
          onChange={handleHighChange}
        />
      </div>

      <div className={styles.labels}>
        <span>0.00</span>
        <span>{(med / 100).toFixed(2)}</span>
        <span>{(high / 100).toFixed(2)}</span>
        <span>1.00</span>
      </div>

      <div className={styles.zoneLabels}>
        <div className={styles.zoneLabelLow} style={{ flex: med }}>Low</div>
        <div className={styles.zoneLabelMed} style={{ flex: high - med }}>Medium</div>
        <div className={styles.zoneLabelHigh} style={{ flex: 100 - high }}>High</div>
      </div>

      <div className={styles.actions}>
        <Button variant="secondary" size="sm" onClick={handleReset}>Reset defaults</Button>
        <Button
          variant="primary"
          size="sm"
          onClick={handleSave}
          loading={saving}
          success={successFor(status, 'save')}
          successLabel="Saved"
        >
          Save
        </Button>
      </div>

      {error && <div className={styles.errorMsg}>{error}</div>}
    </div>
  );
}
