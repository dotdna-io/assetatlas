// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { CHART_TYPES, parseSize } from '../data/chartConfig';
import { findEmptyPosition } from '../grid/useGridCollisions';
import type { ChartConfig } from '../grid/ChartCard';
import styles from './ChartWizard.module.css';

interface ChartWizardProps {
  isOpen: boolean;
  onClose: () => void;
  charts: ChartConfig[];
  onAddChart: (chart: ChartConfig) => void;
}

export function ChartWizard({ isOpen, onClose, charts, onAddChart }: ChartWizardProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const visibleCharts = CHART_TYPES.filter((c) => !c.hidden);
  const generalCharts = visibleCharts.filter((c) => c.category === 'general');
  const chronicleCharts = visibleCharts.filter((c) => c.category === 'chronicle');

  const handleAdd = () => {
    if (!selectedId) return;
    const chartType = CHART_TYPES.find((c) => c.chartId === selectedId);
    if (!chartType) return;

    const { cols, rows } = parseSize(chartType.defaultSize);
    const position = findEmptyPosition(cols, rows, charts);

    const newChart: ChartConfig = {
      id: Date.now(),
      chartId: chartType.chartId,
      className: chartType.className,
      name: chartType.name,
      size: chartType.defaultSize,
      allowedSizes: chartType.allowedSizes,
      defaultOptions: { ...chartType.defaultOptions },
      ...position,
    };

    onAddChart(newChart);
    setSelectedId(null);
    onClose();
  };

  const renderSection = (title: string, types: typeof CHART_TYPES) => (
    <div className={styles.section}>
      <h3 className={styles.sectionTitle}>{title}</h3>
      <div className={styles.typeGrid}>
        {types.map((ct) => (
          <div
            key={ct.chartId}
            className={`${styles.typeCard} ${selectedId === ct.chartId ? styles.selected : ''}`}
            onClick={() => setSelectedId(ct.chartId)}
          >
            <img src={`/chart-icons/${ct.icon}`} alt={ct.name} className={styles.typeIcon} />
            <div className={styles.typeInfo}>
              <span className={styles.typeName}>{ct.name}</span>
              <span className={styles.typeDesc}>{ct.description}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Add Chart" size="xxl">
      <div className={styles.body}>
        {renderSection('Current State', generalCharts)}
        {chronicleCharts.length > 0 && renderSection('Trends Over Time', chronicleCharts)}
      </div>
      <div className={styles.footer}>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={handleAdd} disabled={!selectedId}>Add to Report</Button>
      </div>
    </Modal>
  );
}
