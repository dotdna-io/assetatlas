// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useId, useRef } from 'react';
import { useDarkModeContext } from '@/hooks';

/**
 * Chart instance interface covering the union of methods across all DotDNA charts.
 * Every chart has destroy(); setData/update/resize vary by chart type.
 */
interface ChartInstance {
  destroy(): void;
  setData?(data: any): void;
  update?(options: Record<string, any>): void;
  resize?(gridSize: string): void;
}

interface DotDNAChartProps {
  /** Chart class name (e.g., 'DotDNAAnimatedDonutModern') */
  type: string;
  /** Chart constructor options */
  config: Record<string, any>;
  /** Chart data */
  data: any;
  /** Grid size string, e.g. '2x2', '3x2' -- triggers resize */
  gridSize?: string;
  className?: string;
}

/**
 * Generic wrapper for DotDNA vanilla-JS chart instances.
 *
 * Lifecycle:
 *   mount  -> new ChartClass(containerId, config), registered with DotDNA global
 *   data   -> chart.setData(data) or chart.update({...config, data})
 *   resize -> chart.resize(gridSize)
 *   dark   -> re-render via setData or resize
 *   unmount -> chart.destroy(), unregistered from DotDNA global
 */
export function DotDNAChart({ type, config, data, gridSize, className }: DotDNAChartProps) {
  const reactId = useId();
  // Stable container ID derived from React's useId (strip colons for valid HTML id)
  const containerId = `dotdna-${reactId.replace(/:/g, '')}`;

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ChartInstance | null>(null);
  const configRef = useRef(config);
  configRef.current = config;
  // Tracks the data value the current chart instance was constructed with.
  // Used to skip the redundant initial setData() on mount — the chart class
  // already has this data via the constructor, calling setData() again would
  // play an extra animation.
  const constructedWithDataRef = useRef<any>(undefined);
  const darkModeInitial = useRef(true);

  // --- Mount / unmount ---
  useEffect(() => {
    const ChartClass = (window as any)[type];
    if (!ChartClass) {
      console.error(`DotDNAChart: class "${type}" not found on window`);
      return;
    }

    // Pass data into the constructor so the chart doesn't enter its showNoData()
    // path when data is still loading. showNoData() wipes container.innerHTML,
    // orphaning lineGroup/svg/etc. so a later setData() draws into detached
    // nodes and the tile silently stays blank. setData([]) in the constructor
    // path is a no-op render but keeps the DOM intact for later updates.
    const chart: ChartInstance = new ChartClass(containerId, { ...config, data: data ?? [] });
    chartRef.current = chart;
    constructedWithDataRef.current = data;

    const registry = (window as any).DotDNA;
    if (registry?.register) {
      registry.register(containerId, chart);
    }

    return () => {
      chart.destroy();
      if (registry?.destroyChart) {
        registry.destroyChart(containerId);
      } else {
        registry?.charts?.delete?.(containerId);
      }
      chartRef.current = null;
    };
    // Re-mount only when the chart type or container changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, containerId]);

  // --- Data changes ---
  useEffect(() => {
    const chart = chartRef.current;
    // Skip null too: setData(null) is rejected by every chart class and would
    // log a misleading "requires an array" error during loading transitions.
    if (!chart || data == null) return;

    // Skip if `data` has the same content as what was last applied (initially
    // the constructor data, then whatever the previous setData received).
    // Reference-equality alone misses the case where a key-driven remount
    // is followed by a fresh fetch returning the same value in a new array
    // reference — without this, the chart animates twice.
    if (
      constructedWithDataRef.current !== undefined &&
      JSON.stringify(data) === JSON.stringify(constructedWithDataRef.current)
    ) {
      return;
    }

    // Delay setData so the browser paints the empty chart first,
    // ensuring CSS transitions animate visibly from the initial state.
    const id = setTimeout(() => {
      if (chart.setData) {
        chart.setData(data);
      } else if (chart.update) {
        chart.update({ ...configRef.current, data });
      }
      constructedWithDataRef.current = data;
    }, 150);
    return () => clearTimeout(id);
    // Only react to data changes, not config reference changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  // --- Grid size changes ---
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !gridSize) return;

    if (chart.resize) {
      chart.resize(gridSize);
    }
  }, [gridSize]);

  // --- Dark mode reactivity ---
  const { isDarkMode } = useDarkModeContext();

  useEffect(() => {
    // Skip initial mount — the data effect already handles first render
    if (darkModeInitial.current) {
      darkModeInitial.current = false;
      return;
    }

    const chart = chartRef.current;
    if (!chart) return;

    // Re-render the chart so it picks up the new body class.
    // Prefer setData (re-draws with current data), fall back to resize.
    if (chart.setData && data !== undefined) {
      chart.setData(data);
    } else if (chart.resize && gridSize) {
      chart.resize(gridSize);
    }
    // Only react to dark mode flips, not data/gridSize reference changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDarkMode]);

  return <div ref={containerRef} id={containerId} className={className} />;
}
