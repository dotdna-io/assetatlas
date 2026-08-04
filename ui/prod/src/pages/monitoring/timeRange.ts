// SPDX-License-Identifier: AGPL-3.0-or-later
import type { CustomRange, RangeParams, RangePreset, RefreshInterval } from './types';

const PRESET_TO_SECONDS: Record<Exclude<RangePreset, 'custom'>, number> = {
  '1h': 60 * 60,
  '6h': 6 * 60 * 60,
  '24h': 24 * 60 * 60,
  '7d': 7 * 24 * 60 * 60,
};

const PRESET_TO_STEP: Record<Exclude<RangePreset, 'custom'>, string> = {
  '1h': '15s',
  '6h': '1m',
  '24h': '5m',
  '7d': '30m',
};

// Rate window is the lookback for rate()/increase(). Must be ≥4× scrape interval
// (currently 15s) — never less than 1m — so derivatives always see ≥2 samples.
// Picked larger-than-step for higher presets to smooth out noise.
const PRESET_TO_RATE_WINDOW: Record<Exclude<RangePreset, 'custom'>, string> = {
  '1h': '1m',
  '6h': '5m',
  '24h': '15m',
  '7d': '1h',
};

function customStep(spanSeconds: number): string {
  const target = Math.round(spanSeconds / 240);
  const clamped = Math.max(15, Math.min(target, 30 * 60));
  if (clamped < 60) return `${clamped}s`;
  return `${Math.round(clamped / 60)}m`;
}

// Rate window for custom ranges: max(1m, 4× step), capped at 1h.
function customRateWindow(spanSeconds: number): string {
  const stepSec = Math.max(15, Math.min(Math.round(spanSeconds / 240), 30 * 60));
  const target = Math.max(60, Math.min(stepSec * 4, 60 * 60));
  if (target < 60) return `${target}s`;
  if (target < 3600) return `${Math.round(target / 60)}m`;
  return `${Math.round(target / 3600)}h`;
}

export function rangeToParams(
  preset: RangePreset,
  custom?: CustomRange,
  now: number = Math.floor(Date.now() / 1000),
): RangeParams {
  if (preset === 'custom' && custom) {
    const span = custom.end - custom.start;
    return {
      start: String(custom.start),
      end: String(custom.end),
      step: customStep(span),
      rateWindow: customRateWindow(span),
    };
  }
  if (preset === 'custom') {
    return rangeToParams('1h', undefined, now);
  }
  const span = PRESET_TO_SECONDS[preset];
  return {
    start: String(now - span),
    end: String(now),
    step: PRESET_TO_STEP[preset],
    rateWindow: PRESET_TO_RATE_WINDOW[preset],
  };
}

export const PRESETS: Array<{ id: RangePreset; label: string }> = [
  { id: '1h', label: 'Last 1h' },
  { id: '6h', label: 'Last 6h' },
  { id: '24h', label: 'Last 24h' },
  { id: '7d', label: 'Last 7d' },
  { id: 'custom', label: 'Custom' },
];

export function refreshToMs(interval: RefreshInterval): number | false {
  switch (interval) {
    case 'off': return false;
    case '10s': return 10_000;
    case '30s': return 30_000;
    case '1m':  return 60_000;
    case '5m':  return 5 * 60_000;
  }
}
