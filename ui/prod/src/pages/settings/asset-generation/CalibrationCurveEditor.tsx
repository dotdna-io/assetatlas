// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useUpdateSetting, useResetSetting } from '@/api/settings';
import { Button } from '@/components/ui/Button';
import { InlineStatus, successFor, useInlineStatus } from '@/components/ui/InlineStatus';
import styles from './CalibrationCurveEditor.module.css';

const PADDING = { top: 20, right: 20, bottom: 40, left: 50 };
const WIDTH = 460;
const HEIGHT = 340;
const PLOT_W = WIDTH - PADDING.left - PADDING.right;
const PLOT_H = HEIGHT - PADDING.top - PADDING.bottom;

function dataToSvg(x: number, y: number): [number, number] {
  return [PADDING.left + x * PLOT_W, PADDING.top + (1 - y) * PLOT_H];
}

function svgToDataY(py: number): number {
  return Math.max(0, Math.min(1, 1 - (py - PADDING.top) / PLOT_H));
}

interface Point {
  x: number;
  y: number;
}

interface CalibrationCurveEditorProps {
  settings: Record<string, any> | undefined;
}

export function CalibrationCurveEditor({ settings }: CalibrationCurveEditorProps) {
  const [status, setStatus] = useInlineStatus();
  const updateSetting = useUpdateSetting();
  const resetSetting = useResetSetting();

  const curveSetting = settings?.calibration_curve;
  const curveValue = curveSetting?.value;

  const savedPoints = useMemo<Point[]>(() => {
    if (!curveValue || typeof curveValue !== 'object') return [];
    return Object.keys(curveValue)
      .map((k) => ({ x: parseFloat(k), y: curveValue[k] as number }))
      .sort((a, b) => a.x - b.x);
  }, [curveValue]);

  const [points, setPoints] = useState<Point[]>(savedPoints);
  const [dragging, setDragging] = useState<number | null>(null);
  const [tooltipInfo, setTooltipInfo] = useState<{ x: number; y: number; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (savedPoints.length > 0) setPoints(savedPoints);
  }, [savedPoints]);

  // Grid lines
  const gridLines = useMemo(() => {
    const lines: Array<{ x1: number; y1: number; x2: number; y2: number; cls: string }> = [];
    for (let i = 0; i <= 10; i++) {
      const v = i / 10;
      const cls = i % 5 === 0 ? 'grid-major' : 'grid-minor';
      const [gx] = dataToSvg(v, 0);
      const [, gy] = dataToSvg(0, v);
      lines.push({ x1: gx, y1: PADDING.top, x2: gx, y2: HEIGHT - PADDING.bottom, cls });
      lines.push({ x1: PADDING.left, y1: gy, x2: WIDTH - PADDING.right, y2: gy, cls });
    }
    return lines;
  }, []);

  // Axis labels
  const axisLabels = useMemo(() => {
    const labels: Array<{ x: number; y: number; text: string; anchor: 'middle' | 'end' | 'start' }> = [];
    for (let i = 0; i <= 10; i += 2) {
      const v = i / 10;
      const [gx] = dataToSvg(v, 0);
      const [, gy] = dataToSvg(0, v);
      labels.push({ x: gx, y: HEIGHT - PADDING.bottom + 16, text: v.toFixed(1), anchor: 'middle' });
      labels.push({ x: PADDING.left - 8, y: gy + 3, text: v.toFixed(1), anchor: 'end' });
    }
    return labels;
  }, []);

  // Paths
  const curvePath = useMemo(() => {
    if (points.length === 0) return '';
    return 'M ' + points.map((p) => {
      const [sx, sy] = dataToSvg(p.x, p.y);
      return sx + ' ' + sy;
    }).join(' L ');
  }, [points]);

  const areaPath = useMemo(() => {
    if (points.length === 0) return '';
    const [startX] = dataToSvg(points[0].x, 0);
    let path = 'M ' + startX + ' ' + dataToSvg(0, 0)[1];
    points.forEach((p) => {
      const [sx, sy] = dataToSvg(p.x, p.y);
      path += ' L ' + sx + ' ' + sy;
    });
    const [endX] = dataToSvg(points[points.length - 1].x, 0);
    path += ' L ' + endX + ' ' + dataToSvg(0, 0)[1] + ' Z';
    return path;
  }, [points]);

  // Diagonal reference line
  const [refStart] = [dataToSvg(0, 0)];
  const [refEnd] = [dataToSvg(1, 1)];
  const diagPath = `M ${refStart[0]} ${refStart[1]} L ${refEnd[0]} ${refEnd[1]}`;

  // Pointer event helpers
  const getSvgPoint = useCallback((e: React.PointerEvent<SVGSVGElement>): { x: number; y: number } => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const rect = svg.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }, []);

  const handlePointerDown = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    const target = e.target as SVGElement;
    if (!target.classList.contains('curve-point')) return;
    e.preventDefault();
    const idx = parseInt(target.dataset.idx || '0', 10);
    setDragging(idx);
    target.classList.add('dragging');
    (e.target as SVGElement).setPointerCapture(e.pointerId);
  }, []);

  const handlePointerMove = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    if (dragging === null) return;
    e.preventDefault();
    const pt = getSvgPoint(e);
    let newY = Math.round(svgToDataY(pt.y) * 100) / 100;

    // Enforce monotonicity
    setPoints((prev) => {
      const next = [...prev];
      const minY = dragging > 0 ? next[dragging - 1].y : 0;
      const maxY = dragging < next.length - 1 ? next[dragging + 1].y : 1;
      newY = Math.max(minY, Math.min(maxY, newY));
      next[dragging] = { ...next[dragging], y: newY };

      // Update tooltip
      const [tx, ty] = dataToSvg(next[dragging].x, newY);
      setTooltipInfo({
        x: tx - 30,
        y: ty - 32,
        text: next[dragging].x.toFixed(1) + ' \u2192 ' + newY.toFixed(2),
      });

      return next;
    });
  }, [dragging, getSvgPoint]);

  const handlePointerUp = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    if (dragging !== null) {
      const circles = svgRef.current?.querySelectorAll('.curve-point');
      circles?.[dragging]?.classList.remove('dragging');
      setDragging(null);
      setTooltipInfo(null);
      (e.target as SVGElement).releasePointerCapture?.(e.pointerId);
    }
  }, [dragging]);

  // Save
  const handleSave = useCallback(async () => {
    setSaving(true);
    const newCurve: Record<string, number> = {};
    points.forEach((p) => {
      newCurve[p.x.toFixed(1)] = Math.round(p.y * 100) / 100;
    });
    try {
      await updateSetting.mutateAsync({
        category: 'asset_generation',
        key: 'calibration_curve',
        data: { value: newCurve, reason: 'Updated from Settings UI' },
      });
      setStatus({ kind: 'success', message: 'Calibration curve saved', action: 'save' });
    } catch {
      setStatus({ kind: 'error', message: 'Failed to save calibration curve' });
    } finally {
      setSaving(false);
    }
  }, [points, updateSetting, setStatus]);

  // Reset
  const handleReset = useCallback(() => {
    resetSetting.mutate(
      { category: 'asset_generation', key: 'calibration_curve', reason: 'Reset from Settings UI' },
      {
        onSuccess: () => setStatus({ kind: 'success', message: 'Calibration curve reset', action: 'reset' }),
        onError: () => setStatus({ kind: 'error', message: 'Failed to reset' }),
      },
    );
  }, [resetSetting, setStatus]);

  if (!curveSetting || points.length === 0) return null;

  return (
    <div className={styles.card}>
      <InlineStatus status={status} />
      <div className={styles.description}>
        Drag points to adjust how raw scores map to calibrated output. The dashed diagonal is y=x (no calibration).
      </div>

      <div className={styles.editor}>
        <div className={styles.graphWrap}>
          <svg
            ref={svgRef}
            width={WIDTH}
            height={HEIGHT}
            className={styles.svg}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            style={{ touchAction: 'none' }}
          >
            {/* Grid */}
            {gridLines.map((l, i) => (
              <line key={i} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} className={l.cls} />
            ))}

            {/* Axis labels */}
            {axisLabels.map((l, i) => (
              <text key={i} x={l.x} y={l.y} textAnchor={l.anchor} className="axis-label">
                {l.text}
              </text>
            ))}

            {/* Axis titles */}
            <text
              x={PADDING.left + PLOT_W / 2}
              y={HEIGHT - 4}
              textAnchor="middle"
              className="title-label"
            >
              Raw Confidence
            </text>
            <text
              x={12}
              y={PADDING.top + PLOT_H / 2}
              textAnchor="middle"
              className="title-label"
              transform={`rotate(-90, 12, ${PADDING.top + PLOT_H / 2})`}
            >
              Calibrated
            </text>

            {/* Diagonal reference */}
            <path d={diagPath} className="ref-line" />

            {/* Filled area under curve */}
            <path d={areaPath} className="curve-area" />

            {/* Curve line */}
            <path d={curvePath} className="curve-line" />

            {/* Draggable points */}
            {points.map((p, idx) => {
              const [cx, cy] = dataToSvg(p.x, p.y);
              return (
                <circle
                  key={idx}
                  cx={cx}
                  cy={cy}
                  r={7}
                  className="curve-point"
                  data-idx={idx}
                />
              );
            })}
          </svg>

          {/* Tooltip */}
          {tooltipInfo && (
            <div
              className={styles.tooltipVisible}
              style={{ left: tooltipInfo.x, top: tooltipInfo.y }}
            >
              {tooltipInfo.text}
            </div>
          )}
        </div>

        {/* Value table */}
        <div className={styles.valueTable}>
          <table className={styles.curveTable}>
            <thead>
              <tr>
                <th>Raw</th>
                <th>Out</th>
                <th>Shift</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p) => {
                const diff = p.y - p.x;
                const arrow = diff > 0 ? '\u2191' : diff < 0 ? '\u2193' : '\u2192';
                const shiftClass = diff > 0 ? styles.shiftUp : diff < 0 ? styles.shiftDown : styles.shiftNeutral;
                return (
                  <tr key={p.x}>
                    <td>{p.x.toFixed(1)}</td>
                    <td>{p.y.toFixed(2)}</td>
                    <td className={shiftClass}>
                      {arrow}{diff >= 0 ? '+' : ''}{diff.toFixed(2)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className={styles.actions}>
        <Button
          variant="primary"
          size="sm"
          onClick={handleSave}
          loading={saving}
          success={successFor(status, 'save')}
          successLabel="Saved"
        >
          Save Curve
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={handleReset}
          loading={resetSetting.isPending}
          success={successFor(status, 'reset')}
        >
          Reset to Defaults
        </Button>
      </div>
    </div>
  );
}
