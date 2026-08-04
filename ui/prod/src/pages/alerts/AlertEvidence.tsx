// SPDX-License-Identifier: AGPL-3.0-or-later
import styles from './alerts.module.css';

/**
 * Renders an anomaly alert's `evidence` payload (spec §9): a plain-language
 * narrative (suppressed when it duplicates the `reason` already shown above
 * it in the drilldown), a plain observed-vs-usual stats line, an optional
 * breakdown table, and the full evidence JSON collapsed behind "Technical
 * details" for anyone who wants the underlying stats (z-score, MAD,
 * seasonality, …). Text, table, and a linked asset sample in v1.
 */
export function AlertEvidence({
  evidence,
  reason,
}: {
  evidence: Record<string, any>;
  reason?: string | null;
}) {
  const breakdown = Array.isArray(evidence.breakdown) ? evidence.breakdown : [];
  const usual = evidence.typical ?? evidence.technical?.median;
  return (
    <div className={styles.evidenceBlock}>
      <span className={styles.expandedLabel}>Evidence</span>
      {evidence.narrative && evidence.narrative !== reason && (
        <p className={styles.evidenceNarrative}>{evidence.narrative}</p>
      )}
      {evidence.observed !== undefined && (
        <p className={styles.evidenceStats}>
          Observed <strong>{evidence.observed}</strong>
          {usual !== undefined && <> vs the usual <strong>{usual}</strong></>}
        </p>
      )}
      {breakdown.length > 0 && (
        <table className={styles.evidenceTable}>
          <tbody>
            {breakdown.slice(0, 8).map((row: any, i: number) => (
              <tr key={row.key ?? i}>
                <td>{row.key}</td>
                <td>{row.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {Array.isArray(evidence.sample) && evidence.sample.length > 0 && (
        <div className={styles.evidenceSample}>
          <span className={styles.expandedLabel}>
            Sample of affected assets
            {evidence.observed !== undefined
              ? ` (${evidence.sample.length} of ${evidence.observed})` : ''}
          </span>
          <ul className={styles.evidenceSampleList}>
            {evidence.sample.slice(0, 25).map((s: any) => (
              <li key={s.id}>
                <a href={`/asset-details?id=${s.id}`} target="_blank" rel="noopener noreferrer">
                  {s.label}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
      <details className={styles.evidenceDetails}>
        <summary>Technical details</summary>
        <pre className={styles.evidencePre}>{JSON.stringify(evidence, null, 2)}</pre>
      </details>
    </div>
  );
}
