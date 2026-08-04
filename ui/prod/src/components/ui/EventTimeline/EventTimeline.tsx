// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo, useRef, useState, useCallback } from 'react';
import styles from './EventTimeline.module.css';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TimelineEvent {
  id: string;
  timestamp: string;
  type: 'attribute' | 'alert';
  action?: string;
  attribute?: string;
  alert_type?: string;
}

type ActionType = 'updated' | 'added' | 'removed' | 'alert';

interface Bucket {
  time: number;
  displayTime: number;
  events: TimelineEvent[];
  lane: 'attribute' | 'alert';
  /** Count per action type for concentric rings */
  actionCounts: Partial<Record<ActionType, number>>;
}

interface EventTimelineProps {
  events: TimelineEvent[];
  minTime?: number;
  maxTime?: number;
  height?: number;
  onBucketClick?: (date: string, events: TimelineEvent[]) => void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Ring order: outermost first */
const RING_ORDER: ActionType[] = ['updated', 'added', 'removed', 'alert'];

const ACTION_CONFIG: Record<ActionType, { color: string; label: string; icon: string }> = {
  updated: {
    color: '#3b82f6',
    label: 'Updated',
    icon: 'M21 2v6h-6M3 12a9 9 0 0 1 15-6.7L21 8M3 22v-6h6M21 12a9 9 0 0 1-15 6.7L3 16',
  },
  added: {
    color: '#10b981',
    label: 'Added',
    icon: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM12 8v8M8 12h8',
  },
  removed: {
    color: '#f59e0b',
    label: 'Removed',
    icon: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM8 12h8',
  },
  alert: {
    color: '#ef4444',
    label: 'Alert',
    icon: 'M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 0 1-3.46 0',
  },
};

const PADDING = { top: 20, right: 40, bottom: 35, left: 140 };
const ALERT_LANE_Y = 30;
const CHANGE_LANE_Y = 60;
const AXIS_Y = 105;
const DEFAULT_HEIGHT = 135;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getActionType(event: TimelineEvent): ActionType {
  if (event.type === 'alert') return 'alert';
  const action = (event.action || 'updated').toLowerCase();
  if (action === 'added') return 'added';
  if (action === 'removed') return 'removed';
  return 'updated';
}

function bucketEvents(events: TimelineEvent[], minTime: number, maxTime: number, width: number): Bucket[] {
  const timeRange = maxTime - minTime;
  const rangeDays = timeRange / 86_400_000;
  const usableWidth = width - PADDING.left - PADDING.right;
  const maxBuckets = Math.floor(usableWidth / 44);
  const minBucketMs = timeRange / Math.max(1, maxBuckets);

  let bucketMs: number;
  if (rangeDays <= 1) bucketMs = Math.max(1_800_000, minBucketMs);
  else if (rangeDays <= 7) bucketMs = Math.max(14_400_000, minBucketMs);
  else if (rangeDays <= 30) bucketMs = Math.max(86_400_000, minBucketMs);
  else bucketMs = Math.max(259_200_000, minBucketMs);

  // Group by time key + lane (attribute vs alert)
  const map = new Map<string, Bucket>();

  const useDaily = rangeDays >= 1;

  for (const event of events) {
    const t = new Date(event.timestamp).getTime();
    let key: number;
    if (useDaily) {
      const d = new Date(t);
      key = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    } else {
      key = Math.floor(t / bucketMs) * bucketMs;
    }
    const lane = event.type === 'alert' ? 'alert' : 'attribute';
    const mapKey = `${key}-${lane}`;
    if (!map.has(mapKey)) {
      map.set(mapKey, { time: key, displayTime: key, events: [], lane, actionCounts: {} });
    }
    const bucket = map.get(mapKey)!;
    bucket.events.push(event);
    const actionType = getActionType(event);
    bucket.actionCounts[actionType] = (bucket.actionCounts[actionType] || 0) + 1;
  }

  for (const bucket of map.values()) {
    if (useDaily) {
      bucket.displayTime = bucket.time;
    } else {
      const avg = bucket.events.reduce((s, e) => s + new Date(e.timestamp).getTime(), 0) / bucket.events.length;
      bucket.displayTime = avg;
    }
  }

  return Array.from(map.values());
}

function getBaseSize(count: number, maxCount: number): number {
  const base = 18, max = 36, min = 16;
  if (maxCount <= 1) return base;
  const scale = Math.min(1, (count - 1) / (maxCount - 1));
  return Math.round(min + scale * (max - min));
}

function formatTickLabel(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function formatBucketDate(time: number): string {
  return formatTickLabel(new Date(time));
}

/** Get active action types in a bucket, in ring order */
function getActiveRings(bucket: Bucket): ActionType[] {
  return RING_ORDER.filter((a) => (bucket.actionCounts[a] || 0) > 0);
}

/** Get the dominant (most events) action's icon */
function getDominantAction(bucket: Bucket): ActionType {
  let best: ActionType = bucket.lane === 'alert' ? 'alert' : 'updated';
  let bestCount = 0;
  for (const [action, count] of Object.entries(bucket.actionCounts)) {
    if ((count as number) > bestCount) {
      bestCount = count as number;
      best = action as ActionType;
    }
  }
  return best;
}

// ---------------------------------------------------------------------------
// Legend
// ---------------------------------------------------------------------------

function Legend({ categories }: { categories: Set<ActionType> }) {
  return (
    <div className={styles.legend}>
      {RING_ORDER.map((action) => {
        const cfg = ACTION_CONFIG[action];
        const active = categories.has(action);
        return (
          <div key={action} className={styles.legendItem} style={{ opacity: active ? 1 : 0.3 }}>
            <svg viewBox="0 0 24 24" fill="none" stroke={cfg.color} strokeWidth="2" width="14" height="14" strokeLinecap="round" strokeLinejoin="round">
              <path d={cfg.icon} />
            </svg>
            <span className={styles.legendLabel}>{cfg.label}</span>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bucket SVG rendering
// ---------------------------------------------------------------------------

function BucketIcon({ bucket, x, laneY, baseSize }: { bucket: Bucket; x: number; laneY: number; baseSize: number }) {
  const rings = getActiveRings(bucket);
  const totalCount = bucket.events.length;
  const dominant = getDominantAction(bucket);
  const dominantCfg = ACTION_CONFIG[dominant];

  // Ring radii: outermost = baseSize/2 + 4, each inner ring 5px smaller
  const outerR = baseSize / 2 + 4;
  const ringWidth = rings.length > 1 ? 3.5 : 0;

  // Icon sizing
  const iconSize = Math.max(12, baseSize * 0.6);
  const iconScale = iconSize / 36;

  return (
    <g>
      {/* Connector line */}
      <line
        x1={x} y1={laneY + outerR}
        x2={x} y2={AXIS_Y}
        stroke={dominantCfg.color} strokeWidth={1}
        strokeDasharray="3,3" opacity={0.4}
      />

      {/* Concentric rings — outermost first */}
      {rings.map((action, i) => {
        const r = outerR - i * ringWidth;
        if (r <= 2) return null;
        const cfg = ACTION_CONFIG[action];
        return (
          <circle
            key={action}
            cx={x} cy={laneY}
            r={r}
            fill={i === rings.length - 1 ? 'transparent' : 'transparent'}
            stroke={cfg.color}
            strokeWidth={rings.length > 1 ? 2.5 : 2}
          />
        );
      })}

      {/* Center icon — dominant action */}
      <g transform={`translate(${x - iconSize / 3}, ${laneY - iconSize / 3}) scale(${iconScale})`}>
        <path
          d={dominantCfg.icon}
          fill="none"
          stroke={dominantCfg.color}
          strokeWidth={Math.max(1.5, 2 / iconScale)}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </g>

      {/* Count badge */}
      {totalCount > 1 && (
        <>
          <circle
            cx={x + outerR - 2} cy={laneY - outerR + 2}
            r={Math.min(10, 6 + Math.min(totalCount, 5))}
            fill={dominantCfg.color}
          />
          <text
            x={x + outerR - 2} y={laneY - outerR + 5}
            textAnchor="middle"
            fill="white"
            fontSize={9}
            fontWeight={600}
          >
            {totalCount > 99 ? '99+' : totalCount}
          </text>
        </>
      )}
    </g>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function EventTimeline({ events, minTime: minTimeProp, maxTime: maxTimeProp, height = DEFAULT_HEIGHT, onBucketClick }: EventTimelineProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; bucket: Bucket } | null>(null);
  const [width, setWidth] = useState(800);

  const measuredRef = useCallback((node: HTMLDivElement | null) => {
    if (node) {
      containerRef.current = node;
      const ro = new ResizeObserver((entries) => {
        for (const entry of entries) setWidth(entry.contentRect.width);
      });
      ro.observe(node);
      setWidth(node.clientWidth);
    }
  }, []);

  const timestamps = useMemo(() => events.map((e) => new Date(e.timestamp).getTime()), [events]);
  const rawMin = minTimeProp ?? Math.min(...timestamps, Date.now());
  const rawMax = maxTimeProp ?? Math.max(...timestamps, Date.now());
  // Snap to start/end of day for clean axis alignment
  const minTime = new Date(new Date(rawMin).getFullYear(), new Date(rawMin).getMonth(), new Date(rawMin).getDate()).getTime();
  const maxTime = new Date(new Date(rawMax).getFullYear(), new Date(rawMax).getMonth(), new Date(rawMax).getDate() + 1).getTime();
  const timeRange = maxTime - minTime || 1;

  const buckets = useMemo(() => bucketEvents(events, minTime, maxTime, width), [events, minTime, maxTime, width]);
  const maxCount = Math.max(1, ...buckets.map((b) => b.events.length));
  const activeActions = useMemo(() => {
    const set = new Set<ActionType>();
    for (const b of buckets) {
      for (const a of Object.keys(b.actionCounts)) set.add(a as ActionType);
    }
    return set;
  }, [buckets]);

  const usableWidth = width - PADDING.left - PADDING.right;
  function xPos(t: number): number {
    return PADDING.left + ((t - minTime) / timeRange) * usableWidth;
  }

  // Generate one tick per day, skip if too dense
  const totalDays = Math.round(timeRange / 86_400_000);
  const maxTicks = Math.floor(usableWidth / 100);
  const dayStep = Math.max(1, Math.ceil(totalDays / maxTicks));
  const ticks: { time: number; x: number; label: string }[] = [];
  for (let d = 0; d <= totalDays; d += dayStep) {
    const t = minTime + d * 86_400_000;
    ticks.push({ time: t, x: xPos(t), label: formatTickLabel(new Date(t)) });
  }

  function handleMouseEnter(e: React.MouseEvent, bucket: Bucket) {
    const rect = (e.currentTarget as SVGGElement).getBoundingClientRect();
    const containerRect = containerRef.current?.getBoundingClientRect();
    if (containerRect) {
      setTooltip({
        x: rect.right + 8 - containerRect.left,
        y: rect.top + rect.height / 2 - containerRect.top,
        bucket,
      });
    }
  }

  function handleClick(bucket: Bucket) {
    onBucketClick?.(formatBucketDate(bucket.displayTime), bucket.events);
  }

  return (
    <div ref={measuredRef} className={styles.container}>
      <Legend categories={activeActions} />
      <svg width={width} height={height} className={styles.svg}>
        {/* Axis line */}
        <line
          x1={PADDING.left - 10} y1={AXIS_Y}
          x2={width - PADDING.right + 10} y2={AXIS_Y}
          className={styles.axisLine}
        />

        {/* Ticks + labels */}
        {ticks.map((tick) => (
          <g key={tick.time}>
            <line x1={tick.x} y1={AXIS_Y - 4} x2={tick.x} y2={AXIS_Y + 4} className={styles.tick} />
            <text x={tick.x} y={AXIS_Y + 18} textAnchor="middle" className={styles.tickLabel}>
              {tick.label}
            </text>
          </g>
        ))}

        {/* Event buckets */}
        {buckets.map((bucket) => {
          const x = xPos(bucket.displayTime);
          const laneY = bucket.lane === 'alert' ? ALERT_LANE_Y : CHANGE_LANE_Y;
          const baseSize = getBaseSize(bucket.events.length, maxCount);

          return (
            <g
              key={`${bucket.time}-${bucket.lane}`}
              style={{ cursor: 'pointer' }}
              onMouseEnter={(e) => handleMouseEnter(e, bucket)}
              onMouseLeave={() => setTooltip(null)}
              onClick={() => handleClick(bucket)}
            >
              <BucketIcon bucket={bucket} x={x} laneY={laneY} baseSize={baseSize} />
            </g>
          );
        })}
      </svg>

      {/* Tooltip */}
      {tooltip && (
        <div
          className={styles.tooltip}
          style={{ left: tooltip.x, top: tooltip.y }}
        >
          <div className={styles.tooltipDate}>{formatBucketDate(tooltip.bucket.displayTime)}</div>
          {RING_ORDER.map((action) => {
            const count = tooltip.bucket.actionCounts[action];
            if (!count) return null;
            const cfg = ACTION_CONFIG[action];
            return (
              <div key={action} className={styles.tooltipRow}>
                <span className={styles.tooltipDot} style={{ background: cfg.color }} />
                <span>{count} {cfg.label.toLowerCase()}{count !== 1 ? 's' : ''}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
