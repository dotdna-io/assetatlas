// SPDX-License-Identifier: AGPL-3.0-or-later
import { memo, useMemo } from 'react';

import { DotDNAChart } from '@/components/charts/DotDNAChart';
import { getCategoryColor, isKnownCategory } from '@/components/ui/CategoryIcon';

import type {
  ChartArtifact as ChartArtifactPayload,
  DonutArtifact,
} from './chartArtifactTypes';
import { toDotDNADonut } from './chartArtifactTypes';
import { StackedBarRenderer } from './StackedBarRenderer';
import { TableRenderer } from './TableRenderer';
import styles from './ChartArtifact.module.css';

interface Props {
  artifact: ChartArtifactPayload;
}


function ChartArtifactInner({ artifact }: Props) {
  switch (artifact.type) {
    case 'donut':       return <DonutRenderer artifact={artifact} />;
    case 'stacked_bar': return <StackedBarRenderer artifact={artifact} />;
    case 'table':       return <TableRenderer artifact={artifact} />;
  }
}


function DonutRenderer({ artifact }: { artifact: DonutArtifact }) {
  const data = useMemo(
    () => toDotDNADonut(artifact.series, artifact.meta),
    [artifact.series, artifact.meta],
  );
  // Override per-slice gradient base when the label is a known category
  // so chat donuts use the same palette as the explorer.
  const themed = useMemo(() => data.map((d) => {
    if (d.name === 'Other') return d;
    if (isKnownCategory(d.name)) {
      const color = getCategoryColor(d.name);
      return { ...d, gradient: [color, color] as [string, string] };
    }
    return d;
  }), [data]);
  return (
    <div className={styles.artifact}>
      <div className={styles.title}>{artifact.title}</div>
      <div className={styles.chart}>
        <DotDNAChart
          type="DotDNAAnimatedDonutModern"
          config={{ showLegend: true }}
          data={themed}
          gridSize="2x2"
        />
      </div>
      <DataTable artifact={artifact} />
    </div>
  );
}


// Rendered as a semantic HTML sibling so screen readers reach the
// numbers even though the donut itself is decorative SVG.
function DataTable({ artifact }: { artifact: DonutArtifact }) {
  const total = artifact.meta.total;
  return (
    <details className={styles.tableWrap}>
      <summary>Data ({total.toLocaleString()} total)</summary>
      <table>
        <thead>
          <tr>
            <th scope="col">{artifact.meta.row_field ?? 'Value'}</th>
            <th scope="col" className={styles.numeric}>Count</th>
            <th scope="col" className={styles.numeric}>%</th>
          </tr>
        </thead>
        <tbody>
          {artifact.series.map((slice) => {
            const pct = total > 0 ? (slice.value / total) * 100 : 0;
            return (
              <tr key={slice.label}>
                <td>{slice.label}</td>
                <td className={styles.numeric}>
                  {slice.value.toLocaleString()}
                </td>
                <td className={styles.numeric}>{pct.toFixed(1)}%</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </details>
  );
}


export const ChartArtifact = memo(ChartArtifactInner);
