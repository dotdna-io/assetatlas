// SPDX-License-Identifier: AGPL-3.0-or-later
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import type { WizardFormData } from './wizard-state';
import styles from './ScheduleStep.module.css';

const PRESETS = [
  { label: 'Hourly', cron: '0 * * * *' },
  { label: 'Every 6 Hours', cron: '0 */6 * * *' },
  { label: 'Every 12 Hours', cron: '0 */12 * * *' },
  { label: 'Daily', cron: '0 0 * * *' },
  { label: 'Daily at 2 AM', cron: '0 2 * * *' },
  { label: 'Weekly', cron: '0 0 * * 0' },
  { label: 'Monthly', cron: '0 0 1 * *' },
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
  'Asia/Tokyo',
  'Asia/Shanghai',
  'Australia/Sydney',
];

interface ScheduleStepProps {
  formData: WizardFormData;
  onUpdate: (partial: Partial<WizardFormData>) => void;
}

export function ScheduleStep({ formData, onUpdate }: ScheduleStepProps) {
  const { schedule } = formData;

  const activePreset = PRESETS.find(p => p.cron === schedule.cron);
  const isCustom = schedule.cron && !activePreset;

  const updateSchedule = (patch: Partial<WizardFormData['schedule']>) => {
    onUpdate({ schedule: { ...schedule, ...patch } });
  };

  return (
    <div className={styles.section}>
      <div className={styles.toggleRow}>
        <ToggleSwitch
          checked={schedule.enabled}
          onChange={(val) => updateSchedule({ enabled: val })}
        />
        <span className={styles.toggleLabel}>Enable automatic schedule</span>
      </div>

      {schedule.enabled ? (
        <>
          <div className={styles.presets}>
            {PRESETS.map((preset) => (
              <button
                key={preset.cron}
                type="button"
                className={[
                  styles.presetButton,
                  activePreset?.cron === preset.cron ? styles.presetButtonActive : '',
                ].filter(Boolean).join(' ')}
                onClick={() => updateSchedule({ cron: preset.cron })}
              >
                {preset.label}
              </button>
            ))}
            <button
              type="button"
              className={[
                styles.presetButton,
                isCustom ? styles.presetButtonActive : '',
              ].filter(Boolean).join(' ')}
              onClick={() => updateSchedule({ cron: '' })}
            >
              Custom
            </button>
          </div>

          {(isCustom || (!activePreset && !schedule.cron)) && (
            <div className={styles.field}>
              <div className={styles.fieldLabel}>Cron expression</div>
              <input
                type="text"
                className={styles.cronInput}
                value={schedule.cron}
                onChange={(e) => updateSchedule({ cron: e.target.value })}
                placeholder="e.g. 0 6 * * 1-5"
              />
            </div>
          )}

          <div className={styles.field}>
            <div className={styles.fieldLabel}>Timezone</div>
            <select
              className={styles.timezoneSelect}
              value={schedule.timezone}
              onChange={(e) => updateSchedule({ timezone: e.target.value })}
            >
              {TIMEZONES.map((tz) => (
                <option key={tz} value={tz}>{tz}</option>
              ))}
            </select>
          </div>

          <div className={styles.nextRuns}>
            <div className={styles.nextRunsTitle}>Next runs</div>
            <ul className={styles.nextRunsList}>
              <li className={styles.nextRunItem}>Next runs will be calculated after saving.</li>
            </ul>
          </div>
        </>
      ) : (
        <div className={styles.disabledMessage}>
          No automatic schedule. Data collection will only run when triggered manually.
        </div>
      )}
    </div>
  );
}
