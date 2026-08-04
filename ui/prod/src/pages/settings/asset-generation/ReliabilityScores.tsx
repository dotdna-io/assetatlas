// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  useReliabilityScores,
  useSaveReliabilityScores,
  useResetReliabilityScores,
} from '@/api/settings';
import { Button } from '@/components/ui/Button';
import { InlineStatus, successFor, useInlineStatus } from '@/components/ui/InlineStatus';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import styles from './ReliabilityScores.module.css';

const TIERS = [
  { label: 'Tier 1: On-device agents', categories: ['endpoint security'] },
  { label: 'Tier 2: Infrastructure truth', categories: ['cloud platform', 'mobile device management'] },
  { label: 'Tier 3: Network, identity & control planes', categories: ['network configuration management', 'network traffic', 'identity and access management'] },
  { label: 'Tier 4: Scanning & observational', categories: ['vulnerability management', 'observability platform', 'telemetry derived'] },
  { label: 'Tier 5: Inventory & discovery', categories: ['cmdb', 'asset discovery'] },
  { label: 'Fallback', categories: ['other'] },
];

// Bar fill follows the app's semantic tokens (--success / --warning /
// --error) instead of off-palette teals and yellows. Three bands; the
// transitional bands use a gradient so the bar still reads as a smooth
// continuum rather than three hard color steps.
function barColor(score: number): string {
  if (score >= 0.70) return 'var(--success)';
  if (score >= 0.40) return 'linear-gradient(90deg, var(--warning), var(--success))';
  return 'linear-gradient(90deg, var(--error), var(--warning))';
}

function titleCase(str: string): string {
  return str.replace(/\b\w/g, (c) => c.toUpperCase());
}

export function ReliabilityScores() {
  const [status, setStatus] = useInlineStatus();
  const { data, isLoading } = useReliabilityScores();
  const saveMutation = useSaveReliabilityScores();
  const resetMutation = useResetReliabilityScores();

  const serverScores = useMemo<Record<string, number>>(() => {
    const val = data?.value;
    if (!val || typeof val !== 'object') return {};
    return val as Record<string, number>;
  }, [data]);

  const [scores, setScores] = useState<Record<string, number>>(serverScores);

  useEffect(() => {
    if (Object.keys(serverScores).length > 0) setScores(serverScores);
  }, [serverScores]);

  const handleSliderChange = useCallback((category: string, value: number) => {
    setScores((prev) => ({ ...prev, [category]: value / 100 }));
  }, []);

  const handleSave = useCallback(() => {
    saveMutation.mutate(scores, {
      onSuccess: () => setStatus({ kind: 'success', message: 'Reliability scores saved', action: 'save' }),
      onError: () => setStatus({ kind: 'error', message: 'Failed to save scores' }),
    });
  }, [scores, saveMutation, setStatus]);

  const handleReset = useCallback(() => {
    resetMutation.mutate(undefined, {
      onSuccess: () => setStatus({ kind: 'success', message: 'Scores reset to defaults', action: 'reset' }),
      onError: () => setStatus({ kind: 'error', message: 'Failed to reset scores' }),
    });
  }, [resetMutation, setStatus]);

  if (isLoading) return <LoadingSpinner />;
  if (Object.keys(scores).length === 0) return null;

  return (
    <div className={styles.card}>
      <InlineStatus status={status} />
      <div className={styles.description}>
        How much each data source category is trusted. Drag the bars to adjust. Higher = more authoritative.
      </div>

      {TIERS.map((tier) => (
        <div key={tier.label} className={styles.tierGroup}>
          <div className={styles.tierLabel}>{tier.label}</div>
          {tier.categories.map((cat) => {
            const score = scores[cat] ?? 0.5;
            const pct = Math.round(score * 100);
            return (
              <div key={cat} className={styles.scoreRow}>
                <span className={styles.scoreName}>{titleCase(cat)}</span>
                <div className={styles.barTrack}>
                  <div
                    className={styles.barFill}
                    style={{ width: pct + '%', background: barColor(score) }}
                  />
                  <input
                    type="range"
                    className={styles.slider}
                    min={0}
                    max={100}
                    step={5}
                    value={pct}
                    onChange={(e) => handleSliderChange(cat, parseInt(e.target.value, 10))}
                  />
                </div>
                <span className={styles.scoreValue}>{score.toFixed(2)}</span>
              </div>
            );
          })}
        </div>
      ))}

      <div className={styles.actions}>
        <Button
          variant="primary"
          size="sm"
          onClick={handleSave}
          loading={saveMutation.isPending}
          success={successFor(status, 'save')}
          successLabel="Saved"
        >
          Save Scores
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={handleReset}
          loading={resetMutation.isPending}
          success={successFor(status, 'reset')}
        >
          Reset to Defaults
        </Button>
      </div>
    </div>
  );
}
