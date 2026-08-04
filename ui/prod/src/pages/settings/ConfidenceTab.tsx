// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/ConfidenceTab.tsx
//
// "Confidence" sub-tab under Data Pipeline. Hosts the three sections
// that used to sit at the bottom of the Pipeline tab — they're a
// distinct concern (per-source trust + threshold tuning) so they live
// under their own sub-tab now.
import { useCategorySettings } from '@/api/settings';
import {
  SettingsSection,
  SettingsFullWidthBlock,
} from '@/components/settings';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { ConfidenceThresholds } from './asset-generation/ConfidenceThresholds';
import { CalibrationCurveEditor } from './asset-generation/CalibrationCurveEditor';
import { ReliabilityScores } from './asset-generation/ReliabilityScores';

export function ConfidenceTab() {
  const { data, isLoading } = useCategorySettings('asset_generation');

  if (isLoading) {
    return <LoadingSpinner />;
  }

  const settings = data?.settings;

  return (
    <>
      <SettingsSection title="Confidence Thresholds">
        <SettingsFullWidthBlock>
          <ConfidenceThresholds settings={settings} />
        </SettingsFullWidthBlock>
      </SettingsSection>

      <SettingsSection title="Confidence Calibration">
        <SettingsFullWidthBlock>
          <CalibrationCurveEditor settings={settings} />
        </SettingsFullWidthBlock>
      </SettingsSection>

      <SettingsSection title="Source Reliability Scores">
        <SettingsFullWidthBlock>
          <ReliabilityScores />
        </SettingsFullWidthBlock>
      </SettingsSection>
    </>
  );
}
