// SPDX-License-Identifier: AGPL-3.0-or-later
/* Alert rule categories ("type" on rules and on the alerts they fire):
   see AlertSettingsStep's Category dropdown and alert_rules.type. */

export const ALERT_TYPE_COLORS: Record<string, string> = {
  security: '#ef4444',
  compliance: '#f97316',
  operational: '#64748b',
  data_quality: '#15803d',
};

export const ALERT_TYPE_LABELS: Record<string, string> = {
  security: 'Security',
  compliance: 'Compliance',
  operational: 'Operational',
  data_quality: 'Data quality',
};

export const ALERT_TYPES = ['security', 'compliance', 'operational', 'data_quality'] as const;

export type AlertType = (typeof ALERT_TYPES)[number];
