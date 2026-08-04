// SPDX-License-Identifier: AGPL-3.0-or-later
import { memo, useMemo } from 'react';

import { DotDNAChart } from '@/components/charts/DotDNAChart';
import {
  getCategoryColor,
  isKnownCategory,
} from '@/components/ui/CategoryIcon';

import type { StackedBarArtifact } from './chartArtifactTypes';
import { toDotDNAStackedColumn } from './chartArtifactTypes';
import styles from './ChartArtifact.module.css';

interface Props {
  artifact: StackedBarArtifact;
}

/**
 * Renders a 2-D stacked-bar artifact via DotDNAStackedColumn. The bar
 * x-axis comes from ``artifact.series[].x_label``; each stack inside a
 * bar comes from ``artifact.series[].stacks[]``. Stack labels that
 * match a known asset-category get the shared explorer color so chat
 * + explorer use the same palette.
 */
function StackedBarRendererInner({ artifact }: Props) {
  const data = useMemo(
    () => toDotDNAStackedColumn(artifact.series),
    [artifact.series],
  );
  // Per-segment colors only for stacks whose label is a known
  // asset-category — others fall back to the chart's default palette.
  const segmentColors = useMemo(() => {
    const out: Record<string, string> = {};
    for (const bar of artifact.series) {
      for (const s of bar.stacks) {
        if (isKnownCategory(s.label)) {
          out[s.label] = getCategoryColor(s.label);
        }
      }
    }
    return out;
  }, [artifact.series]);

  return (
    <div className={styles.artifact}>
      <div className={styles.title}>{artifact.title}</div>
      <div className={styles.chart}>
        <DotDNAChart
          type="DotDNAStackedColumn"
          config={{ segmentColors }}
          data={data}
          gridSize="3x2"
        />
      </div>
    </div>
  );
}

export const StackedBarRenderer = memo(StackedBarRendererInner);
