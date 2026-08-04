// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import { ConnectionConfigStep } from './ConnectionConfigStep';
import { CustomHttpConfigStep, CustomHttpPreviewBody } from './CustomHttpConfigStep';
import { ConnectionTestStep, extractFieldsRecursive } from './ConnectionTestStep';
import { ScheduleStep } from './ScheduleStep';
import type { WizardFormData, WizardMode, WizardState, CustomHttpConfig } from './wizard-state';
import { DEFAULT_CUSTOM_HTTP_CONFIG } from './wizard-state';
import { integrationsApi } from '@/api/integrations';
import type { CustomHttpTestResponse } from '@/api/integrations';
import styles from './ConnectionStep.module.css';

interface ConnectionStepProps {
  formData: WizardFormData;
  onUpdate: (partial: Partial<WizardFormData>) => void;
  mode: WizardMode;
  integrationId: string | null;
  connectionStatus: WizardState['connectionTestStatus'];
  onStatusChange: (status: WizardState['connectionTestStatus']) => void;
}

export function ConnectionStep(props: ConnectionStepProps) {
  if (props.formData.integration_type === 'custom_http') {
    return <CustomHttpConnectionStep {...props} />;
  }
  return <StandardConnectionStep {...props} />;
}

function StandardConnectionStep({
  formData, onUpdate, mode, integrationId, connectionStatus, onStatusChange,
}: ConnectionStepProps) {
  return (
    <div className={styles.layout}>
      {/* Left: Connection Configuration */}
      <div className={styles.panel}>
        <div className={styles.panelHeader}>Connection Settings</div>
        <div className={styles.panelBody}>
          <ConnectionConfigStep formData={formData} onUpdate={onUpdate} mode={mode} />
        </div>
      </div>

      {/* Right: Test + Schedule */}
      <div className={styles.rightColumn}>
        <div className={styles.section}>
          <div className={styles.sectionHeader}>
            <span className={styles.sectionTitle}>Connection Test</span>
          </div>
          <div className={styles.sectionBody}>
            <ConnectionTestStep formData={formData} onUpdate={onUpdate}
              integrationId={integrationId} mode={mode}
              connectionStatus={connectionStatus} onStatusChange={onStatusChange} />
          </div>
        </div>

        <div className={styles.section}>
          <div className={styles.sectionHeader}>
            <span className={styles.sectionTitle}>Schedule</span>
          </div>
          <div className={styles.sectionBody}>
            <ScheduleStep formData={formData} onUpdate={onUpdate} />
          </div>
        </div>
      </div>
    </div>
  );
}

/** custom_http layout — 50/50, all config on the left, single Preview-response
 *  block on the right that doubles as the connection test (it populates
 *  sourceFields/sampleData and flips connectionTestStatus to 'success' so the
 *  Next button gates on it the same way as for other connectors). */
function CustomHttpConnectionStep({
  formData, onUpdate, mode, integrationId, onStatusChange,
}: ConnectionStepProps) {
  const cfg = (formData.config as Partial<CustomHttpConfig>) ?? {};
  const merged: CustomHttpConfig = { ...DEFAULT_CUSTOM_HTTP_CONFIG, ...cfg };

  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<CustomHttpTestResponse | null>(null);

  const onTest = async () => {
    setTesting(true);
    try {
      const r = await integrationsApi.testCustomHttp(merged, integrationId);
      setTestResult(r);

      // Populate sourceFields + sampleData so step 3 mapping works without
      // requiring a separate Connection Test pass. Fields come from the
      // sample records the connector returns under the resolved records-root.
      if (r.success) {
        const sampleRecords =
          (r.records_root_match?.sample_records ?? []) as Record<string, any>[];

        let sourceFields: string[];
        if (merged.passthrough) {
          sourceFields = extractFieldsRecursive(
            sampleRecords,
            merged.passthrough_depth ?? 3,
          );
        } else {
          // Bindings mode — source fields are exactly the binding names. We
          // still hand the raw sample records to step 3 so the user has
          // something to inspect; coverage stats won't match the binding
          // names but the mapping list itself is what matters.
          sourceFields = (merged.field_bindings ?? [])
            .map((b) => b.source_name)
            .filter(Boolean);
        }

        onUpdate({ sampleData: sampleRecords, sourceFields });
        onStatusChange('success');
      } else {
        onStatusChange('failed');
      }
    } catch {
      onStatusChange('failed');
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className={styles.layoutEqual}>
      {/* Left: connection setup only — Endpoint, Auth, Pagination, Body. */}
      <div className={styles.panel}>
        <div className={styles.panelHeader}>Connection Settings</div>
        <div className={styles.panelBody}>
          <CustomHttpConfigStep
            formData={formData} onUpdate={onUpdate} mode={mode}
            testing={testing} testResult={testResult} onTest={onTest}
            hideTestSection
            placement="left"
          />
        </div>
      </div>

      {/* Right: Output (with required-choice text) + Preview + Schedule.
          Records-root + Field bindings live inside the Output card since
          they're all about how each record is shaped. */}
      <div className={styles.rightColumn}>
        <div className={styles.section}>
          <div className={styles.sectionHeader}>
            <span className={styles.sectionTitle}>Output</span>
          </div>
          <div className={styles.sectionBody}>
            <CustomHttpConfigStep
              formData={formData} onUpdate={onUpdate} mode={mode}
              testing={testing} testResult={testResult} onTest={onTest}
              hideTestSection
              placement="right"
            />
          </div>
        </div>

        <div className={styles.section}>
          <div className={styles.sectionHeader}>
            <span className={styles.sectionTitle}>Preview response</span>
          </div>
          <div className={styles.sectionBody}>
            <CustomHttpPreviewBody cfg={merged} testing={testing}
              testResult={testResult} onTest={onTest} />
          </div>
        </div>

        <div className={styles.section}>
          <div className={styles.sectionHeader}>
            <span className={styles.sectionTitle}>Schedule</span>
          </div>
          <div className={styles.sectionBody}>
            <ScheduleStep formData={formData} onUpdate={onUpdate} />
          </div>
        </div>
      </div>
    </div>
  );
}
