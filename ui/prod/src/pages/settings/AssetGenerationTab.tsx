// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useCallback } from 'react';
import { useCategorySettings, useUpdateSetting } from '@/api/settings';
import {
  SettingsSection,
  SettingsRowGrid,
  SettingsRow,
  SettingsFullWidthBlock,
} from '@/components/settings';
import { Button } from '@/components/ui/Button';
import { InlineStatus, successFor, useInlineStatus } from '@/components/ui/InlineStatus';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { PipelineOperations } from './asset-generation/PipelineOperations';
import { PipelineSchedule } from './asset-generation/PipelineSchedule';
import styles from './AssetGenerationTab.module.css';

export function AssetGenerationTab() {
  const { data, isLoading } = useCategorySettings('asset_generation');
  const updateSetting = useUpdateSetting();
  const updateGraceSetting = useUpdateSetting();
  const [status, setStatus] = useInlineStatus();
  const [graceStatus, setGraceStatus] = useInlineStatus();

  const settings = data?.settings;
  const mappingSetting = settings?.mapping_expiration_days;
  const graceperiodSetting = settings?.removal_grace_period_days;

  const [mappingDays, setMappingDays] = useState<number | ''>('');
  const displayDays =
    mappingDays !== '' ? mappingDays : (mappingSetting?.value ?? '');

  const handleMappingSave = useCallback(() => {
    const val =
      typeof displayDays === 'number'
        ? displayDays
        : parseInt(String(displayDays), 10);
    if (isNaN(val)) return;
    updateSetting.mutate(
      {
        category: 'asset_generation',
        key: 'mapping_expiration_days',
        data: { value: val, reason: 'Updated from Settings UI' },
      },
      {
        onSuccess: () => setStatus({ kind: 'success', message: 'Mapping expiration saved', action: 'save' }),
        onError: () => setStatus({ kind: 'error', message: 'Failed to save' }),
      },
    );
  }, [displayDays, updateSetting, setStatus]);

  const [graceDays, setGraceDays] = useState<number | ''>('');
  const displayGraceDays =
    graceDays !== '' ? graceDays : (graceperiodSetting?.value ?? '');

  const handleGraceSave = useCallback(() => {
    const val =
      typeof displayGraceDays === 'number'
        ? displayGraceDays
        : parseInt(String(displayGraceDays), 10);
    if (isNaN(val)) return;
    updateGraceSetting.mutate(
      {
        category: 'asset_generation',
        key: 'removal_grace_period_days',
        data: { value: val, reason: 'Updated from Settings UI' },
      },
      {
        onSuccess: () => setGraceStatus({ kind: 'success', message: 'Removal grace period saved', action: 'save' }),
        onError: () => setGraceStatus({ kind: 'error', message: 'Failed to save' }),
      },
    );
  }, [displayGraceDays, updateGraceSetting, setGraceStatus]);

  if (isLoading) {
    return <LoadingSpinner />;
  }

  const mappingMeta = mappingSetting
    ? [
        `Default: ${mappingSetting.default}`,
        mappingSetting.min != null ? `Min: ${mappingSetting.min}` : null,
        mappingSetting.max != null ? `Max: ${mappingSetting.max}` : null,
      ]
        .filter(Boolean)
        .join(' | ')
    : '';

  const graceMeta = graceperiodSetting
    ? [
        `Default: ${graceperiodSetting.default}`,
        graceperiodSetting.min != null ? `Min: ${graceperiodSetting.min}` : null,
        graceperiodSetting.max != null ? `Max: ${graceperiodSetting.max}` : null,
      ]
        .filter(Boolean)
        .join(' | ')
    : '';

  return (
    <>
      <InlineStatus status={status} />
      <InlineStatus status={graceStatus} />
      {/* PipelineOperations renders its own glossy card with the
          "Pipeline" title + subtitle + Run Now button as the header,
          so it's not wrapped in an outer SettingsSection — that would
          double up the card surface. */}
      <PipelineOperations />

      <SettingsSection title="Pipeline Schedule">
        <SettingsFullWidthBlock>
          <PipelineSchedule settings={settings} />
        </SettingsFullWidthBlock>
      </SettingsSection>

      {(mappingSetting || graceperiodSetting) && (
        <SettingsSection title="Configuration">
          <SettingsRowGrid>
            {mappingSetting && (
              <SettingsRow
                name="Mapping Expiration Days"
                description={mappingSetting.description || ''}
                meta={mappingMeta}
                control={
                  <div className={styles.inlineControl}>
                    <input
                      type="number"
                      className={styles.numberInput}
                      value={displayDays}
                      min={mappingSetting.min || 0}
                      max={mappingSetting.max || 999}
                      onChange={(e) => {
                        const v = e.target.value;
                        setMappingDays(v === '' ? '' : parseInt(v, 10));
                      }}
                    />
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={handleMappingSave}
                      loading={updateSetting.isPending}
                      success={successFor(status, 'save')}
                      successLabel="Saved"
                    >
                      Save
                    </Button>
                  </div>
                }
              />
            )}
            {graceperiodSetting && (
              <SettingsRow
                name="Removal grace period (days)"
                description="Days an absent asset is kept before it's removed. 0 = remove immediately."
                meta={graceMeta}
                control={
                  <div className={styles.inlineControl}>
                    <input
                      type="number"
                      className={styles.numberInput}
                      value={displayGraceDays}
                      min={graceperiodSetting.min ?? 0}
                      max={graceperiodSetting.max ?? 999}
                      onChange={(e) => {
                        const v = e.target.value;
                        setGraceDays(v === '' ? '' : parseInt(v, 10));
                      }}
                    />
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={handleGraceSave}
                      loading={updateGraceSetting.isPending}
                      success={successFor(graceStatus, 'save')}
                      successLabel="Saved"
                    >
                      Save
                    </Button>
                  </div>
                }
              />
            )}
          </SettingsRowGrid>
        </SettingsSection>
      )}
    </>
  );
}
