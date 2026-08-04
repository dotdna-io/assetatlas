// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useMemo, useCallback } from 'react';
import { useUpdateSetting } from '@/api/settings';
import { usePipelineStatus } from '@/api/pipeline';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import { Button } from '@/components/ui/Button';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import styles from './PipelineSchedule.module.css';

const INTERVAL_OPTIONS = [1, 2, 4, 6, 8, 12, 24, 48, 72, 168];

const CRON_PRESETS = [
  { value: '0 * * * *', label: 'Every hour' },
  { value: '0 */2 * * *', label: 'Every 2 hours' },
  { value: '0 */4 * * *', label: 'Every 4 hours' },
  { value: '0 */6 * * *', label: 'Every 6 hours' },
  { value: '0 */12 * * *', label: 'Every 12 hours' },
  { value: '0 0 * * *', label: 'Daily at midnight' },
  { value: '0 2 * * *', label: 'Daily at 2 AM' },
  { value: '0 6 * * *', label: 'Daily at 6 AM' },
  { value: '0 0 * * 0', label: 'Weekly (Sunday)' },
  { value: '0 0 * * 1', label: 'Weekly (Monday)' },
  { value: '0 0 1 * *', label: 'Monthly (1st)' },
  { value: 'custom', label: 'Custom...' },
];

const TIMEZONES = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Europe/Bucharest',
  'Asia/Tokyo',
  'Asia/Shanghai',
  'Australia/Sydney',
];

function formatNextRun(isoString: string | null | undefined): string {
  if (!isoString) return '--';
  const diffMs = new Date(isoString).getTime() - Date.now();
  if (diffMs <= 0) return 'any moment';
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return 'less than a minute';
  if (mins < 60) return 'in ' + mins + 'm';
  const hours = Math.floor(mins / 60);
  const rm = mins % 60;
  const rel = rm > 0 ? 'in ' + hours + 'h ' + rm + 'm' : 'in ' + hours + 'h';
  const d = new Date(isoString);
  const ts = d.getFullYear() + '-' +
    String(d.getMonth() + 1).padStart(2, '0') + '-' +
    String(d.getDate()).padStart(2, '0') + ' ' +
    String(d.getHours()).padStart(2, '0') + ':' +
    String(d.getMinutes()).padStart(2, '0');
  return rel + ' (' + ts + ')';
}

interface PipelineScheduleProps {
  settings: Record<string, any> | undefined;
}

export function PipelineSchedule({ settings }: PipelineScheduleProps) {
  const [statusMsg, setStatusMsg] = useInlineStatus();
  const updateSetting = useUpdateSetting();
  const { data: status } = usePipelineStatus();

  // Parse saved values from settings
  const saved = useMemo(() => ({
    enabled: (() => {
      const v = settings?.pipeline_schedule_enabled?.value;
      return v === true || v === 'true';
    })(),
    mode: (settings?.pipeline_schedule_mode?.value as string) || 'interval',
    interval_hours: Number(settings?.pipeline_schedule_interval_hours?.value) || 24,
    cron: (settings?.pipeline_schedule_cron?.value as string) || '0 0 * * *',
    timezone: (settings?.pipeline_schedule_timezone?.value as string) || 'UTC',
  }), [settings]);

  const [enabled, setEnabled] = useState(saved.enabled);
  const [mode, setMode] = useState(saved.mode);
  const [intervalHours, setIntervalHours] = useState(saved.interval_hours);
  const [customCron, setCustomCron] = useState('');
  const [timezone, setTimezone] = useState(saved.timezone);
  const [saving, setSaving] = useState(false);

  // Determine if cron value matches a preset
  const [cronPreset, setCronPreset] = useState<string>(() => {
    const match = CRON_PRESETS.find((p) => p.value === saved.cron && p.value !== 'custom');
    return match ? match.value : 'custom';
  });

  // Sync from server when settings load/change
  useEffect(() => {
    setEnabled(saved.enabled);
    setMode(saved.mode);
    setIntervalHours(saved.interval_hours);
    setTimezone(saved.timezone);
    const match = CRON_PRESETS.find((p) => p.value === saved.cron && p.value !== 'custom');
    if (match) {
      setCronPreset(match.value);
      setCustomCron('');
    } else {
      setCronPreset('custom');
      setCustomCron(saved.cron);
    }
  }, [saved]);

  const currentCron = cronPreset === 'custom' ? customCron.trim() : cronPreset;

  // Dirty detection
  const isDirty = useMemo(() => {
    if (mode !== saved.mode) return true;
    if (mode === 'interval') {
      return intervalHours !== saved.interval_hours;
    }
    if (currentCron !== saved.cron) return true;
    if (timezone !== saved.timezone) return true;
    return false;
  }, [mode, saved, intervalHours, currentCron, timezone]);

  const handleEnabledChange = useCallback((checked: boolean) => {
    setEnabled(checked);
    updateSetting.mutate(
      { category: 'asset_generation', key: 'pipeline_schedule_enabled', data: { value: checked, reason: 'Updated from Settings UI' } },
      {
        onError: () => {
          setEnabled(!checked);
          setStatusMsg({ kind: 'error', message: 'Failed to update schedule' });
        },
      },
    );
  }, [updateSetting, setStatusMsg]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      const modeUpdate = updateSetting.mutateAsync({
        category: 'asset_generation',
        key: 'pipeline_schedule_mode',
        data: { value: mode, reason: 'Updated from Settings UI' },
      });
      if (mode === 'interval') {
        await Promise.all([
          modeUpdate,
          updateSetting.mutateAsync({
            category: 'asset_generation',
            key: 'pipeline_schedule_interval_hours',
            data: { value: intervalHours, reason: 'Updated from Settings UI' },
          }),
        ]);
      } else {
        await Promise.all([
          modeUpdate,
          updateSetting.mutateAsync({
            category: 'asset_generation',
            key: 'pipeline_schedule_cron',
            data: { value: currentCron, reason: 'Updated from Settings UI' },
          }),
          updateSetting.mutateAsync({
            category: 'asset_generation',
            key: 'pipeline_schedule_timezone',
            data: { value: timezone, reason: 'Updated from Settings UI' },
          }),
        ]);
      }
      setStatusMsg({ kind: 'success', message: 'Schedule saved' });
    } catch {
      setStatusMsg({ kind: 'error', message: 'Failed to save schedule' });
    } finally {
      setSaving(false);
    }
  }, [mode, intervalHours, currentCron, timezone, updateSetting, setStatusMsg]);

  const nextRunAt = status?.next_run_at;

  return (
    <div className={styles.card}>
      <InlineStatus status={statusMsg} />
      <div className={styles.row}>
        {/* Enable toggle */}
        <div className={styles.toggle}>
          <span className={styles.toggleLabel}>Scheduled Runs</span>
          <ToggleSwitch checked={enabled} onChange={handleEnabledChange} />
        </div>

        {/* Controls */}
        <div className={enabled ? styles.controls : styles.controlsDisabled}>
          {/* Mode tabs */}
          <div className={styles.modeTabs}>
            <button
              className={mode === 'interval' ? styles.modeTabActive : styles.modeTab}
              onClick={() => setMode('interval')}
              type="button"
            >
              Interval
            </button>
            <button
              className={mode === 'cron' ? styles.modeTabActive : styles.modeTab}
              onClick={() => setMode('cron')}
              type="button"
            >
              Cron
            </button>
          </div>

          {/* Interval controls */}
          {mode === 'interval' && (
            <div className={styles.inlineGroup}>
              <label>Every</label>
              <select
                className={styles.input}
                value={String(intervalHours)}
                onChange={(e) => setIntervalHours(parseInt(e.target.value, 10))}
              >
                {INTERVAL_OPTIONS.map((h) => (
                  <option key={h} value={String(h)}>{h}</option>
                ))}
              </select>
              <label>hours</label>
            </div>
          )}

          {/* Cron controls */}
          {mode === 'cron' && (
            <div className={styles.inlineGroup}>
              <select
                className={styles.input}
                value={cronPreset}
                onChange={(e) => {
                  setCronPreset(e.target.value);
                  if (e.target.value !== 'custom') {
                    setCustomCron('');
                  }
                }}
              >
                {CRON_PRESETS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
              {cronPreset === 'custom' && (
                <input
                  type="text"
                  className={styles.inputMono}
                  placeholder="0 */4 * * *"
                  value={customCron}
                  onChange={(e) => setCustomCron(e.target.value)}
                />
              )}
              <select
                className={styles.input}
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
              >
                {TIMEZONES.map((tz) => (
                  <option key={tz} value={tz}>{tz}</option>
                ))}
              </select>
            </div>
          )}

          {/* Save button (only when dirty) */}
          {isDirty && (
            <Button
              variant="primary"
              size="sm"
              onClick={handleSave}
              loading={saving}
            >
              {saving ? 'Saving...' : 'Save'}
            </Button>
          )}
        </div>

        {/* Next Run */}
        <div className={enabled && nextRunAt ? styles.nextRun : styles.nextRunDisabled}>
          <span className={styles.nextRunLabel}>Next run:</span>
          <span>{enabled ? formatNextRun(nextRunAt) : '--'}</span>
        </div>
      </div>
    </div>
  );
}
