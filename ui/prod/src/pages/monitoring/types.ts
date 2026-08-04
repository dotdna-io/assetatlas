// SPDX-License-Identifier: AGPL-3.0-or-later
export type TabId = 'system' | 'database' | 'redis' | 'pipeline' | 'host' | 'containers';

export type RangePreset = '1h' | '6h' | '24h' | '7d' | 'custom';

export interface RangeParams {
  start: string;
  end: string;
  /** Resolution of the time series — gap between datapoints. */
  step: string;
  /** Lookback window for rate()/increase() — must be ≥4× scrape interval (15s)
   *  so a derivative always sees at least 2 samples. Distinct from step. */
  rateWindow: string;
}

export interface CustomRange {
  start: number;
  end: number;
}

export type RefreshInterval = 'off' | '10s' | '30s' | '1m' | '5m';
