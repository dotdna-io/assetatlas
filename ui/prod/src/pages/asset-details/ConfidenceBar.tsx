// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import type { AssetDetail, ConfidenceBreakdown } from '@/api/asset-details';
import { Badge } from '@/components/ui/Badge';
import styles from './asset-details.module.css';

interface ConfidenceBarProps {
  asset: AssetDetail;
}

// ---------------------------------------------------------------------------
// Factor card helper
// ---------------------------------------------------------------------------

interface FactorDef {
  label: string;
  getValue: (b: ConfidenceBreakdown) => string;
  getDetail: (b: ConfidenceBreakdown) => string;
  getScore: (b: ConfidenceBreakdown) => number; // 0-1 for bar width
}

const FACTORS: FactorDef[] = [
  {
    label: 'Sources',
    getValue: (b) => `${b.source_count} of ${b.available_sources}`,
    getDetail: (b) => `Score: ${Math.round(b.source_count_score * 100)}%`,
    getScore: (b) => b.source_count_score,
  },
  {
    label: 'Quality',
    getValue: (b) => `${Math.round(b.source_reliability * 100)}%`,
    getDetail: () => 'Source reliability',
    getScore: (b) => b.source_reliability,
  },
  {
    label: 'Completeness',
    getValue: (b) => `${Math.round(b.data_completeness * 100)}%`,
    getDetail: (b) => b.missing_fields.length > 0
      ? `Missing: ${b.missing_fields.slice(0, 3).join(', ')}${b.missing_fields.length > 3 ? '...' : ''}`
      : 'All fields present',
    getScore: (b) => b.data_completeness,
  },
  {
    label: 'Consistency',
    getValue: (b) => b.conflicting_fields.length > 0
      ? `${b.conflicting_fields.length} conflicts`
      : 'No conflicts',
    getDetail: (b) => `Penalty: ${Math.round(b.consistency_penalty * 100)}%`,
    getScore: (b) => 1 - b.consistency_penalty,
  },
  {
    label: 'Recency',
    getValue: (b) => `${Math.round((1 - b.recency_penalty) * 100)}%`,
    getDetail: (b) => `Penalty: ${Math.round(b.recency_penalty * 100)}%`,
    getScore: (b) => 1 - b.recency_penalty,
  },
  {
    label: 'Identity',
    getValue: (b) => b.identifier_type,
    getDetail: (b) => `Cap: ${Math.round(b.identifier_cap * 100)}%`,
    getScore: (b) => b.identifier_cap,
  },
];

function getScoreColor(score: number): string {
  if (score >= 0.8) return 'var(--success)';
  if (score >= 0.5) return 'var(--warning)';
  return 'var(--error)';
}

function getConfidenceVariant(percent: number): 'success' | 'warning' | 'danger' {
  if (percent >= 80) return 'success';
  if (percent >= 50) return 'warning';
  return 'danger';
}

function FactorCard({ factor, breakdown }: { factor: FactorDef; breakdown: ConfidenceBreakdown }) {
  const score = factor.getScore(breakdown);
  const barColor = getScoreColor(score);

  return (
    <div className={styles.factorCard}>
      <div className={styles.factorLabel}>{factor.label}</div>
      <div className={styles.factorValue}>{factor.getValue(breakdown)}</div>
      <div className={styles.factorBar}>
        <div
          className={styles.factorBarFill}
          style={{ width: `${Math.round(score * 100)}%`, background: barColor }}
        />
      </div>
      <div className={styles.factorDetail}>{factor.getDetail(breakdown)}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

export function ConfidenceBar({ asset }: ConfidenceBarProps) {
  const [expanded, setExpanded] = useState(false);
  const percent = Math.round(asset.confidence * 100);
  const variant = getConfidenceVariant(percent);

  return (
    <div className={styles.confidenceSection}>
      <div className={styles.confidenceBar} onClick={() => setExpanded(!expanded)}>
        <div className={styles.confidenceLeft}>
          <Badge variant={variant}>{asset.confidence_level}</Badge>
          <span className={styles.confidencePercent}>{percent}%</span>
          {asset.confidence_explanation && (
            <span className={styles.confidenceExplanation}>{asset.confidence_explanation}</span>
          )}
        </div>
        <button className={styles.chevronBtn} title={expanded ? 'Collapse' : 'Expand'}>
          <svg
            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
            width="16" height="16"
            style={{ transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      </div>

      {expanded && asset.confidence_breakdown && (
        <div className={styles.factorsGrid}>
          {FACTORS.map((f) => (
            <FactorCard key={f.label} factor={f} breakdown={asset.confidence_breakdown!} />
          ))}
        </div>
      )}
    </div>
  );
}
