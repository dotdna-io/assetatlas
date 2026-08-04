// SPDX-License-Identifier: AGPL-3.0-or-later
import { useDocumentTitle } from '@/hooks';
import { useReducer, useEffect, useRef, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { useCreateIntegration, useAvailableConnectors, integrationsApi } from '@/api/integrations';
import { getApiErrorMessage } from '@/api/client';
import { getConnectorDisplayName, getSourceMethodLabel } from './integrations/integration-utils';
import {
  wizardReducer,
  initialState,
  saveWizardState,
  clearWizardState,
} from './integration-wizard/wizard-state';
import type { WizardMode, WizardStep, WizardFormData } from './integration-wizard/wizard-state';
import { WizardProgress } from './integration-wizard/WizardProgress';
import { TypeSelector } from './integration-wizard/TypeSelector';
import { BasicConfigStep } from './integration-wizard/BasicConfigStep';
import { ConnectionStep } from './integration-wizard/ConnectionStep';
import { MappingStep } from './integration-wizard/MappingStep';
import { ReviewStep } from './integration-wizard/ReviewStep';
import styles from './IntegrationWizardPage.module.css';

function buildFieldMappingPayload(formData: WizardFormData): Record<string, string | string[]> {
  const mapping: Record<string, string | string[]> = {};
  const mappedSources = new Set<string>();
  // Group by target — multiple sources per target become an array
  for (const m of formData.field_mapping) {
    const existing = mapping[m.target];
    if (existing) {
      mapping[m.target] = Array.isArray(existing) ? [...existing, m.source] : [existing, m.source];
    } else {
      mapping[m.target] = m.source;
    }
    mappedSources.add(m.source);
  }
  // Preserve user-selected data source specific fields as self-mappings
  for (const field of formData.preserve_fields) {
    if (!mappedSources.has(field)) {
      mapping[field] = field;
    }
  }
  return mapping;
}

/**
 * Build the create/update request body from wizard form data. Exported for
 * unit testing — keep in sync with the fields the backend create/update
 * handlers read. `source_method` must be included so "via SIEM/datalake"
 * integrations route to the transport connector instead of the technology
 * (otherwise the backend defaults it to 'direct' and the saved integration
 * fails every sync with "Unknown integration_type").
 */
/**
 * Drop `_`-prefixed wizard scratch keys before the config is persisted.
 *
 * The wizard uses that prefix for values it needs in the browser but the
 * backend must never see — the same convention `extractFromObject` already
 * applies when discovering source fields. Nothing enforced it on save, so the
 * manual-upload step's `_file` (a browser File) was JSON-serialized into the
 * integration's config as the literal `{}` and stored (#1133). Harmless-looking,
 * but it is how a non-serializable value reached the database at all.
 */
export function stripInternalConfigKeys(
  config: Record<string, any>,
): Record<string, any> {
  return Object.fromEntries(
    Object.entries(config || {}).filter(([key]) => !key.startsWith('_')),
  );
}

export function buildSubmitPayload(formData: WizardFormData, mode: WizardMode) {
  const categoryKey = mode === 'edit' ? 'category' : 'data_source_category';
  return {
    name: formData.name,
    integration_type: formData.integration_type!,
    source_method: formData.source_method,
    config: stripInternalConfigKeys(formData.config),
    field_mapping: buildFieldMappingPayload(formData),
    // Distinct source-of-truth for DSS classification (#809). The DSS fields
    // also stay encoded in field_mapping as {field:field} self-mappings above,
    // so the pipeline is unchanged; preserve_fields lets edit-mode load
    // classify them as "Data Source Specific" even when they are named like a
    // canonical framework field.
    preserve_fields: formData.preserve_fields,
    [categoryKey]: formData.category || undefined,
    logo: formData.logo?.replace(/^\/logos\//, '') || undefined,
    description: formData.description || undefined,
    transformations: formData.transformations.length > 0
      ? Object.fromEntries(
          formData.transformations.map(t => [
            t.name,
            {
              type: 'transform' as const,
              operations: t.operations.map(({ id, ...op }) => op),
            },
          ]),
        )
      : undefined,
    data_filters: formData.data_filters.length > 0
      ? formData.data_filters
      : undefined,
  };
}

export default function IntegrationWizardPage() {
  useDocumentTitle('New Integration');
  const [searchParams] = useSearchParams();
  const mode = (searchParams.get('mode') || 'create') as WizardMode;
  const editId = searchParams.get('id') || undefined;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [status, setStatus] = useInlineStatus();

  const [state, dispatch] = useReducer(wizardReducer, initialState);

  // Always start fresh in create mode
  useEffect(() => {
    if (mode === 'create') {
      clearWizardState();
      dispatch({ type: 'RESET' });
    }
  }, []);

  // Load integration for edit/clone mode
  const [editIntegration, setEditIntegration] = useState<any>(null);
  const [editLoading, setEditLoading] = useState(mode !== 'create');

  useEffect(() => {
    if (mode === 'create' || !editId) return;
    setEditLoading(true);
    integrationsApi.get(editId, mode === 'clone')
      .then((result) => {
        setEditIntegration(result.integration || result);
        setEditLoading(false);
      })
      .catch((err) => {
        setEditLoading(false);
        setStatus({ kind: 'error', message: `Failed to load integration: ${err?.message || 'Unknown error'}` });
        navigate('/integrations');
      });
  }, [editId, mode]);

  useEffect(() => {
    if (editIntegration && (mode === 'edit' || mode === 'clone')) {
      dispatch({ type: 'LOAD_INTEGRATION', payload: { integration: editIntegration, mode } });
    }
  }, [editIntegration, mode]);

  // Persist wizard state to localStorage on changes (create mode only, debounced)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => {
    if (mode === 'create' && state.dirty) {
      clearTimeout(saveTimerRef.current);
      saveTimerRef.current = setTimeout(() => saveWizardState(state), 500);
    }
    return () => clearTimeout(saveTimerRef.current);
  }, [state, mode]);

  // Preview cache cleanup
  function cleanupPreviewCache() {
    if (state.formData.preview_session_id) {
      integrationsApi.deletePreview(state.formData.preview_session_id).catch(() => {});
    }
  }

  // Submit
  const [isSubmitting, setIsSubmitting] = useState(false);

  const createMutation = useCreateIntegration({
    onSuccess: async (result) => {
      const integrationId = result.integration?.id;
      // Disable if user unchecked the enabled toggle
      if (!state.formData.enabled && integrationId) {
        try { await integrationsApi.disable(integrationId); } catch { /* optional */ }
      }
      let scheduleError: string | null = null;
      if (state.formData.schedule.enabled && integrationId) {
        try {
          await integrationsApi.updateSchedule(integrationId, {
            cron: state.formData.schedule.cron,
            timezone: state.formData.schedule.timezone,
            enabled: true,
          });
        } catch (err) {
          // The integration was created but the schedule was rejected (typically
          // an invalid cron). Surface the backend message — silently swallowing
          // it leaves the user with a created-but-unscheduled integration and
          // no idea why.
          scheduleError = getApiErrorMessage(err, 'Failed to save schedule');
        }
      }
      if (integrationId && !scheduleError) {
        try { await integrationsApi.run(integrationId); } catch { /* optional */ }
      }
      // TODO(pipeline-after-onboarding): when the backend supports it and
      // state.formData.run_pipeline_after_create is true, trigger an
      // asset-generation pipeline run here (sequenced after the collection run
      // above). The Review-step checkbox already captures the choice; it is
      // intentionally a no-op until that wiring lands.
      cleanupPreviewCache();
      clearWizardState();
      if (scheduleError) {
        setStatus({
          kind: 'error',
          message: `Integration created, but schedule could not be saved: ${scheduleError}`,
        });
        setIsSubmitting(false);
        // Navigate to the integration's details page so the user has a way
        // to fix the schedule. Otherwise they'd be stuck on an emptied
        // wizard with the integration created but unscheduled.
        if (integrationId) {
          navigate(`/integration-details?id=${integrationId}`);
        }
        return;
      }
      setStatus(null); // the wizard closes onto the integration in the list
      navigate('/integrations');
    },
    onError: (err) => {
      setStatus({ kind: 'error', message: `Failed to create integration: ${err.message}` });
      setIsSubmitting(false);
    },
  });

  function validateAllSteps(): { valid: boolean; errors: { step: number; message: string }[] } {
    const errors: { step: number; message: string }[] = [];

    // Step 1: Basic Details
    if (!state.formData.name.trim()) {
      errors.push({ step: 1, message: 'Integration name is required' });
    }
    if (!state.formData.category) {
      errors.push({ step: 1, message: 'Data source category is required' });
    }

    // Step 2: Connection Config
    if (state.formData.integration_type !== 'manual_upload') {
      const configKeys = Object.keys(state.formData.config).filter(k => !k.startsWith('_'));
      if (configKeys.length === 0) {
        errors.push({ step: 2, message: 'Connection configuration is required' });
      }
    }

    // Step 3: Field Mapping
    if (state.formData.field_mapping.length === 0) {
      errors.push({ step: 3, message: 'At least one field mapping is required' });
    }
    const hasIdentifier = state.formData.field_mapping.some(
      m => ['hostname', 'fqdn', 'ip', 'mac'].includes(m.target)
    );
    if (state.formData.field_mapping.length > 0 && !hasIdentifier) {
      errors.push({ step: 3, message: 'Map at least one identifier (hostname, fqdn, ip, or mac)' });
    }

    // Step 2: Schedule (only if enabled — schedule is now part of step 2)
    if (state.formData.schedule.enabled && !state.formData.schedule.cron.trim()) {
      errors.push({ step: 2, message: 'Schedule is enabled but no cron expression is set' });
    }

    return { valid: errors.length === 0, errors };
  }

  const handleSubmit = () => {
    const { valid, errors } = validateAllSteps();
    if (!valid) {
      const messages = errors.map(e => `Step ${e.step}: ${e.message}`).join('\n');
      setStatus({ kind: 'error', message: messages });
      // Navigate to the first error step
      dispatch({ type: 'SET_STEP', payload: errors[0].step as WizardStep });
      return;
    }
    setIsSubmitting(true);
    const payload = buildSubmitPayload(state.formData, state.mode);

    if (state.mode === 'edit' && state.editIntegrationId) {
      integrationsApi.update(state.editIntegrationId, payload).then(async () => {
        let scheduleError: string | null = null;
        if (state.formData.schedule.enabled) {
          try {
            await integrationsApi.updateSchedule(state.editIntegrationId!, {
              cron: state.formData.schedule.cron,
              timezone: state.formData.schedule.timezone,
              enabled: true,
            });
          } catch (err) {
            scheduleError = getApiErrorMessage(err, 'Failed to save schedule');
          }
        } else {
          try {
            await integrationsApi.disableSchedule(state.editIntegrationId!);
          } catch (err) {
            scheduleError = getApiErrorMessage(err, 'Failed to disable schedule');
          }
        }
        setIsSubmitting(false);
        cleanupPreviewCache();
        clearWizardState();
        queryClient.invalidateQueries({ queryKey: ['integrations'], refetchType: 'all' });
        queryClient.invalidateQueries({ queryKey: ['integration', state.editIntegrationId], refetchType: 'all' });
        if (scheduleError) {
          setStatus({
            kind: 'error',
            message: `Integration updated, but schedule could not be saved: ${scheduleError}`,
          });
          return;
        }
        setStatus(null); // the wizard closes onto the updated integration
        navigate(`/integration-details?id=${state.editIntegrationId}`);
      }).catch((err) => {
        setStatus({ kind: 'error', message: `Failed to update: ${err.message}` });
        setIsSubmitting(false);
      });
    } else {
      createMutation.mutate(payload);
    }
  };

  // Cancel confirmation
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);

  const handleCancel = () => {
    if (state.dirty) {
      setShowCancelConfirm(true);
    } else {
      clearWizardState();
      navigate('/integrations');
    }
  };

  const confirmCancel = () => {
    cleanupPreviewCache();
    clearWizardState();
    navigate('/integrations');
  };

  const isFirstStep = state.currentStep === 'type-select' || state.currentStep === 1;
  const isLastStep = state.currentStep === 4;

  // Block advancing from step 2 unless connection test passed, skipped, manual_upload, or editing
  const isManualUpload = state.formData.integration_type === 'manual_upload';
  const canAdvanceFromStep2 = isManualUpload
    || state.connectionTestStatus === 'success'
    || state.connectionTestStatus === 'skipped'
    || state.mode === 'edit';

  // Connector metadata for the header badge — must be before early returns (hooks rule)
  const { data: connectorsData } = useAvailableConnectors();

  // Per-step required-field validation. Returns the first missing-field
  // reason, or null when the current step is complete enough to advance.
  // Drives the Next-button disabled state so the user can't click through
  // an incomplete step and only see the error at submit time.
  const stepBlockedReason = ((): string | null => {
    const fd = state.formData;
    const step = state.currentStep;

    if (step === 1) {
      if (!fd.name.trim()) return 'Integration name is required';
      if (!fd.category) return 'Data source category is required';
      return null;
    }

    if (step === 2) {
      const cfg = (fd.config ?? {}) as Record<string, any>;

      if (fd.integration_type === 'manual_upload') {
        if (!cfg._file) return 'Upload a file to continue';
      } else if (fd.integration_type === 'custom_http') {
        if (!cfg.url) return 'URL is required';
        const authType = cfg.auth_type;
        if (!authType) return 'Auth type is required';
        if (authType === 'basic' && (!cfg.username || !cfg.password)) {
          return 'Username and password are required';
        }
        if (authType === 'bearer' && !cfg.bearer_token) {
          return 'Bearer token is required';
        }
        if (authType === 'api_key' && !cfg.api_key) {
          return 'API key is required';
        }
        if (authType === 'oauth2' && (!cfg.token_url || !cfg.client_id || !cfg.client_secret)) {
          return 'Token URL, client ID and client secret are required';
        }
        if (authType === 'login_password' && (!cfg.login_url || !cfg.identity || !cfg.password)) {
          return 'Login URL, identity and password are required';
        }
      } else {
        // Standard connectors — required fields come from the connector's
        // config_schema (loaded with the connector list).
        const schemaType = fd.source_method && fd.source_method !== 'direct'
          ? fd.source_method
          : fd.integration_type;
        const connector = connectorsData?.connectors?.find(
          (c: any) => c.integration_type === schemaType,
        );
        const schema = connector?.config_schema;
        if (schema) {
          const requiredList: string[] | undefined = schema.required;
          const properties = schema.properties || schema.fields || schema;
          for (const [key, prop] of Object.entries(properties as Record<string, any>)) {
            if (typeof prop !== 'object' || prop === null) continue;
            const isRequired = prop.required === true
              || (Array.isArray(requiredList) && requiredList.includes(key));
            if (!isRequired) continue;
            const val = cfg[key];
            if (val == null || val === '') {
              const label = prop.label || prop.title
                || key.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase());
              return `${label} is required`;
            }
          }
        }
      }

      if (!canAdvanceFromStep2) {
        return 'Test the connection before proceeding';
      }
      if (fd.schedule.enabled && !fd.schedule.cron.trim()) {
        return 'Schedule is enabled but no cron expression is set';
      }
      return null;
    }

    if (step === 3) {
      if (fd.field_mapping.length === 0) {
        return 'Map at least one field';
      }
      const hasIdentifier = fd.field_mapping.some(
        (m) => ['hostname', 'fqdn', 'ip', 'mac'].includes(m.target),
      );
      if (!hasIdentifier) {
        return 'Map at least one identifier (hostname, fqdn, ip, or mac)';
      }
      return null;
    }

    return null;
  })();

  const handleNext = () => {
    if (typeof state.currentStep === 'number' && state.currentStep < 4) {
      if (stepBlockedReason) {
        setStatus({ kind: 'error', message: stepBlockedReason });
        return;
      }
      dispatch({ type: 'SET_STEP', payload: (state.currentStep + 1) as WizardStep });
    }
  };

  const handlePrev = () => {
    if (typeof state.currentStep === 'number' && state.currentStep > 1) {
      dispatch({ type: 'SET_STEP', payload: (state.currentStep - 1) as WizardStep });
    }
  };
  const activeConnector = connectorsData?.connectors?.find(
    (c: any) => c.integration_type === state.formData.integration_type
  );
  const viaConnector = connectorsData?.connectors?.find(
    (c: any) => c.integration_type === state.formData.source_method
  );

  // Fill in logo from connector when the integration doesn't have one stored
  useEffect(() => {
    if (!state.formData.logo && activeConnector?.logo_url && state.formData.integration_type) {
      dispatch({ type: 'UPDATE_FORM', payload: { logo: activeConnector.logo_url } });
    }
  }, [activeConnector, state.formData.logo, state.formData.integration_type]);

  // Loading state for edit/clone
  if (editLoading) {
    return <LoadingSpinner />;
  }

  // Step rendering
  const renderStep = () => {
    switch (state.currentStep) {
      case 'type-select':
        return <TypeSelector
          onSelectType={(t, sourceMethod) => {
            dispatch({ type: 'SET_SOURCE_METHOD', payload: sourceMethod ?? 'direct' });
            dispatch({ type: 'SET_TYPE', payload: t });
          }}
          selectedType={state.formData.integration_type}
          mode={state.mode}
          initialSourceMethod={state.formData.source_method}
        />;
      case 1:
        return <BasicConfigStep
          formData={state.formData}
          onUpdate={(p) => dispatch({ type: 'UPDATE_FORM', payload: p })}
          mode={state.mode}
        />;
      case 2:
        return <ConnectionStep
          formData={state.formData}
          onUpdate={(p) => dispatch({ type: 'UPDATE_FORM', payload: p })}
          mode={state.mode}
          integrationId={state.editIntegrationId}
          connectionStatus={state.connectionTestStatus}
          onStatusChange={(s) => dispatch({ type: 'SET_CONNECTION_STATUS', payload: s })}
        />;
      case 3:
        return <MappingStep
          formData={state.formData}
          onUpdate={(p) => dispatch({ type: 'UPDATE_FORM', payload: p })}
        />;
      case 4:
        return <ReviewStep
          formData={state.formData}
          mode={mode}
          onGoToStep={(step) => dispatch({ type: 'SET_STEP', payload: step })}
          onUpdate={(p) => dispatch({ type: 'UPDATE_FORM', payload: p })}
        />;
      default:
        return null;
    }
  };

  const isTypeSelect = state.currentStep === 'type-select';
  const title = isTypeSelect
    ? 'Select integration type'
    : mode === 'edit'
    ? `Edit Integration: ${state.formData.name}`
    : mode === 'clone'
    ? 'Clone Integration'
    : 'New Integration';

  return (
    <div className={isTypeSelect ? styles.pageWide : styles.page}>
      <InlineStatus status={status} />
      <div className={styles.titleRow}>
        {isTypeSelect && <div className={styles.title}>{title}</div>}
        {!isTypeSelect && state.formData.integration_type && (
          <div className={styles.connectorBadgeStack}>
            <div className={styles.connectorBadge}>
              {activeConnector?.logo_url && (
                <img src={activeConnector.logo_url} alt="" className={styles.connectorBadgeLogo} />
              )}
              <span className={styles.connectorBadgeName}>
                {activeConnector?.display_name || getConnectorDisplayName(state.formData.integration_type)}
              </span>
            </div>
            {state.formData.source_method && state.formData.source_method !== 'direct' && (
              <div className={styles.viaBadge}>
                <span className={styles.viaLabel}>via</span>
                {viaConnector?.logo_url && (
                  <img src={viaConnector.logo_url} alt="" className={styles.viaBadgeLogo} />
                )}
                <span className={styles.viaBadgeName}>
                  {viaConnector?.display_name || getSourceMethodLabel(state.formData.source_method) || state.formData.source_method}
                </span>
              </div>
            )}
          </div>
        )}
      </div>
      {isTypeSelect && (
        <div className={styles.subtitle}>Connect AssetAtlas to your data sources</div>
      )}

      {state.formData.integration_type && (
        <WizardProgress
          currentStep={state.currentStep}
          integrationType={state.formData.integration_type}
          validation={state.validation}
          mode={state.mode}
          onStepClick={(step) => dispatch({ type: 'SET_STEP', payload: step })}
        />
      )}

      <div className={styles.stepContent}>
        {renderStep()}
      </div>

      {state.currentStep !== 'type-select' && (
        <div className={styles.navigation}>
          <Button variant="ghost" onClick={handleCancel}>
            {mode === 'edit' ? 'Cancel Edit' : 'Cancel'}
          </Button>
          <div className={styles.navRight}>
            {!isFirstStep && (
              <Button variant="secondary" onClick={handlePrev}>Previous</Button>
            )}
            {!isLastStep && (
              <Button variant="primary"
                onClick={handleNext}
                disabled={!!stepBlockedReason}
                title={stepBlockedReason ?? undefined}>
                Next
              </Button>
            )}
            {!isLastStep && mode === 'edit' && (
              <Button variant="secondary" onClick={() => dispatch({ type: 'SET_STEP', payload: 4 })}>
                Skip to Review
              </Button>
            )}
            {isLastStep && (
              <Button variant="primary" onClick={handleSubmit} loading={isSubmitting}>
                {mode === 'edit' ? 'Save Changes' : 'Create Integration'}
              </Button>
            )}
          </div>
        </div>
      )}

      <ConfirmDialog
        isOpen={showCancelConfirm}
        onConfirm={confirmCancel}
        onCancel={() => setShowCancelConfirm(false)}
        title="Discard Changes"
        message="You have unsaved changes. Are you sure you want to leave?"
        variant="warning"
      />
    </div>
  );
}
