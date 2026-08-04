// SPDX-License-Identifier: AGPL-3.0-or-later
export const SEVERITY_COLORS: Record<string, string> = {
  critical: '#8b5cf6',
  high: '#ef4444',
  medium: '#f59e0b',
  low: '#3b82f6',
};

export const SEVERITY_LABELS: Record<string, string> = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
};

export const SEVERITIES = ['critical', 'high', 'medium', 'low'] as const;

export type Severity = (typeof SEVERITIES)[number];
