// SPDX-License-Identifier: AGPL-3.0-or-later
import { useRef, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { integrationsApi } from '@/api/integrations';
import type { WizardFormData, WizardMode, WizardState } from './wizard-state';
import styles from './ConnectionTestStep.module.css';

interface ConnectionTestStepProps {
  formData: WizardFormData;
  onUpdate: (partial: Partial<WizardFormData>) => void;
  integrationId: string | null;
  mode: WizardMode;
  connectionStatus: WizardState['connectionTestStatus'];
  onStatusChange: (status: WizardState['connectionTestStatus']) => void;
}

/** Ceiling for a manually uploaded file.
 *
 * The content is stored inline in the integration's JSONB config, so this is a
 * row-size limit rather than a transfer limit. Refusing with a clear message
 * beats a 500 from the API or a config row nothing can load afterwards. */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

export function extractFromObject(obj: any, prefix: string, depth: number, fields: Set<string>) {
  if (!obj || typeof obj !== 'object' || depth <= 0) return;
  for (const [key, value] of Object.entries(obj)) {
    if (key.startsWith('_')) continue; // skip internal fields
    const path = prefix ? `${prefix}.${key}` : key;
    fields.add(path);
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      extractFromObject(value, path, depth - 1, fields);
    }
  }
}

export function extractFieldsRecursive(records: Record<string, any>[], depth: number = 6): string[] {
  const fieldSet = new Set<string>();
  const maxRecords = Math.min(records.length, 50);
  const safeDepth = Math.max(1, Math.floor(depth));
  for (let i = 0; i < maxRecords; i++) {
    extractFromObject(records[i], '', safeDepth, fieldSet);
  }
  return [...fieldSet].sort();
}

/** Per-integration override for field discovery depth.
 *  Only custom_http exposes this today (when passthrough is enabled). */
function getDiscoveryDepth(formData: WizardFormData): number {
  if (formData.integration_type === 'custom_http') {
    const cfg = formData.config as { passthrough?: boolean; passthrough_depth?: number };
    if (cfg?.passthrough) return cfg.passthrough_depth ?? 3;
  }
  return 6;
}

function parseCSVRow(line: string): string[] {
  const fields: string[] = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const char = line[i];
    if (inQuotes) {
      if (char === '"' && line[i + 1] === '"') { current += '"'; i++; }
      else if (char === '"') { inQuotes = false; }
      else { current += char; }
    } else {
      if (char === '"') { inQuotes = true; }
      else if (char === ',') { fields.push(current); current = ''; }
      else { current += char; }
    }
  }
  fields.push(current);
  return fields;
}

function parseCSV(text: string): Record<string, any>[] {
  const lines = text.split('\n').filter(l => l.trim());
  if (lines.length < 2) return [];
  const headers = parseCSVRow(lines[0]);
  return lines.slice(1, 101).map(line => {
    const values = parseCSVRow(line);
    const record: Record<string, any> = {};
    headers.forEach((h, i) => { record[h.trim()] = values[i]?.trim() ?? ''; });
    return record;
  });
}

function parseJSON(text: string): Record<string, any>[] {
  const trimmed = text.trim();
  // Try standard JSON first
  try {
    const parsed = JSON.parse(trimmed);
    if (Array.isArray(parsed)) return parsed.slice(0, 100);
    if (typeof parsed === 'object') return [parsed];
  } catch {
    // Try NDJSON (one JSON object per line)
    const lines = trimmed.split('\n').filter(l => l.trim());
    const records: Record<string, any>[] = [];
    for (const line of lines.slice(0, 100)) {
      try { records.push(JSON.parse(line)); } catch { /* skip bad lines */ }
    }
    if (records.length > 0) return records;
  }
  return [];
}

export function ConnectionTestStep({
  formData,
  onUpdate,
  integrationId,
  mode,
  connectionStatus,
  onStatusChange,
}: ConnectionTestStepProps) {
  const isManualUpload = formData.integration_type === 'manual_upload';
  const isSkipped = connectionStatus === 'skipped';
  const errorMessageRef = useRef<string>('');
  const testMessageRef = useRef<string>('');
  const sampleInfoRef = useRef<string>('');
  const [fetchingSamples, setFetchingSamples] = useState(false);

  const handleTest = async () => {
    onStatusChange('testing');
    errorMessageRef.current = '';
    let tempId: string | null = null;

    try {
      let testId = integrationId;

      // Create mode: create temp integration, test it, then delete it
      if (!testId) {
        const tempResult = await integrationsApi.create({
          name: `__test_${Date.now()}`,
          integration_type: formData.integration_type!,
          source_method: formData.source_method,
          config: formData.config,
        });
        testId = tempResult.integration?.id;
        tempId = testId; // track for cleanup
      }

      if (!testId) throw new Error('Failed to create test integration');

      // Edit mode: ship the wizard's unsaved edits so the backend tests against
      // them, not the saved row. Create mode's temp integration already carries
      // `formData.config`, so no candidate is needed.
      const candidateConfig = mode === 'edit' ? formData.config : undefined;

      const testResult = await integrationsApi.testConnection(testId, candidateConfig);

      if (testResult.success) {
        testMessageRef.current = testResult.message || 'Successfully connected to the data source';
        onStatusChange('success');
        // Fetch sample data — use 1000 like legacy, fallback to 100 on timeout
        setFetchingSamples(true);
        try {
          let sampleResult;
          let usedFallback = false;
          try {
            sampleResult = await integrationsApi.getSampleData(testId, 1000, candidateConfig);
          } catch {
            sampleResult = await integrationsApi.getSampleData(testId, 100, candidateConfig);
            usedFallback = true;
          }
          const records = sampleResult?.records || sampleResult?.samples || sampleResult?.sample_data || [];
          if (records.length > 0) {
            const fields = extractFieldsRecursive(records, getDiscoveryDepth(formData));
            const totalRecords = sampleResult?.total ?? records.length;
            onUpdate({ sampleData: records, sourceFields: fields });
            sampleInfoRef.current = `Discovered ${fields.length} fields from ${totalRecords.toLocaleString()} total records`;
            if (usedFallback) {
              sampleInfoRef.current += ' (reduced sample due to timeout)';
            }
          } else {
            sampleInfoRef.current = 'No sample data available — source may be empty or not yet synced';
          }
        } catch {
          sampleInfoRef.current = 'Connected but could not retrieve sample data';
        } finally {
          setFetchingSamples(false);
        }
      } else {
        errorMessageRef.current = testResult.message || 'Connection failed';
        onStatusChange('failed');
      }
    } catch (err: any) {
      errorMessageRef.current = err?.message || 'An unexpected error occurred';
      onStatusChange('failed');
    } finally {
      // Clean up temp integration
      if (tempId) {
        try { await integrationsApi.delete(tempId); } catch { /* best effort */ }
      }
    }
  };

  const handleManualFileProcess = () => {
    const file = formData.config._file as File | undefined;
    if (!file) return;

    const reader = new FileReader();
    const ext = file.name.split('.').pop()?.toLowerCase();

    if (file.size > MAX_UPLOAD_BYTES) {
      // The content is inlined into the integration's JSONB config, so an
      // oversized file becomes an oversized row rather than a clean error.
      errorMessageRef.current =
        `File is ${(file.size / 1024 / 1024).toFixed(1)} MB. The limit is ` +
        `${MAX_UPLOAD_BYTES / 1024 / 1024} MB — split it or import via an API connector.`;
      onStatusChange('failed');
      return;
    }

    reader.onload = (e) => {
      const content = e.target?.result as string;
      try {
        let records: Record<string, any>[] = [];
        if (ext === 'json') {
          records = parseJSON(content);
        } else if (ext === 'csv') {
          records = parseCSV(content);
        }
        const sourceFields = extractFieldsRecursive(records, getDiscoveryDepth(formData));
        onUpdate({
          sampleData: records,
          sourceFields,
          // Persist what the CONNECTOR reads, not just what this step renders.
          // Parsing here only ever produced sampleData/sourceFields for the
          // mapping UI; the content was then dropped, so the saved integration
          // had no file at all and every run failed with "No file data
          // provided" (#1133). config._file cannot carry it — a File has no
          // enumerable properties, so JSON.stringify yields {}.
          config: {
            ...formData.config,
            file_data: content,
            file_format: ext,
            file_name: file.name,
          },
        });
        onStatusChange('success');
      } catch {
        errorMessageRef.current = 'Failed to parse file. Please check the format.';
        onStatusChange('failed');
      }
    };
    reader.onerror = () => {
      errorMessageRef.current = 'Failed to read file.';
      onStatusChange('failed');
    };

    if (ext === 'csv' || ext === 'json') {
      reader.readAsText(file);
    } else {
      errorMessageRef.current = 'XLSX files are not supported. Please save as CSV or JSON and re-upload.';
      onStatusChange('failed');
    }
  };

  const handleSkip = () => {
    onStatusChange('skipped');
  };

  if (isManualUpload) {
    // `_fileName` is this session's pick; `file_name` is what a saved
    // integration carries, since `_`-prefixed keys are stripped before save.
    const fileName = (formData.config._fileName
      ?? formData.config.file_name) as string | undefined;
    const hasFile = !!fileName;

    return (
      <div className={styles.testSection}>
        <p className={styles.message}>
          {hasFile
            ? `File selected: ${fileName}`
            : 'No file selected. Go back to step 2 to upload a file.'}
        </p>
        {hasFile && connectionStatus === 'idle' && (
          <Button variant="primary" onClick={handleManualFileProcess}>
            Process File
          </Button>
        )}
        {connectionStatus === 'testing' && (
          <div className={styles.testingRow}>
            <LoadingSpinner size="sm" />
            <span className={styles.message}>Processing file...</span>
          </div>
        )}
        {connectionStatus === 'success' && (
          <>
            <div className={[styles.statusIcon, styles.statusSuccess].join(' ')}><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6L9 17l-5-5"/></svg></div>
            <p className={styles.message}>File processed successfully</p>
            {formData.sampleData && (
              <p className={styles.sampleInfo}>{formData.sampleData.length} records loaded</p>
            )}
          </>
        )}
        {connectionStatus === 'failed' && (
          <>
            <div className={[styles.statusIcon, styles.statusFailed].join(' ')}><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg></div>
            <p className={styles.message}>Processing failed</p>
            {errorMessageRef.current && (
              <p className={styles.sampleInfo}>{errorMessageRef.current}</p>
            )}
          </>
        )}
      </div>
    );
  }

  return (
    <div className={styles.testSection}>
      {isSkipped && (
        <p className={styles.message}>Connection test skipped. You can proceed without testing.</p>
      )}

      {!isSkipped && connectionStatus !== 'testing' && connectionStatus !== 'success' && connectionStatus !== 'failed' && (
        <p className={styles.message}>
          Verify the credentials and confirm AssetAtlas can reach the remote service.
        </p>
      )}

      {connectionStatus === 'testing' && (
        <div className={styles.testingRow}>
          <LoadingSpinner size="sm" />
          <span className={styles.message}>Testing connection...</span>
        </div>
      )}

      {connectionStatus === 'success' && (
        <div className={styles.statusRow}>
          <div className={[styles.statusIcon, styles.statusSuccess].join(' ')}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6L9 17l-5-5"/></svg></div>
          <div>
            <p className={styles.statusText}>Connection Successful</p>
            {testMessageRef.current && <p className={styles.sampleInfo}>{testMessageRef.current}</p>}
            {fetchingSamples ? (
              <div className={styles.testingRow}>
                <LoadingSpinner size="sm" />
                <span className={styles.sampleInfo}>Fetching sample data...</span>
              </div>
            ) : (
              sampleInfoRef.current && <p className={styles.sampleInfo}>{sampleInfoRef.current}</p>
            )}
          </div>
        </div>
      )}

      {connectionStatus === 'failed' && (
        <div className={styles.statusRow}>
          <div className={[styles.statusIcon, styles.statusFailed].join(' ')}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg></div>
          <div>
            <p className={styles.statusText}>Connection failed</p>
            {errorMessageRef.current && (
              <p className={styles.sampleInfo}>{errorMessageRef.current}</p>
            )}
          </div>
        </div>
      )}

      {connectionStatus !== 'testing' && (
        <Button
          variant={connectionStatus === 'idle' && !isSkipped ? 'primary' : 'secondary'}
          size="sm"
          onClick={handleTest}
          disabled={isSkipped}
        >
          {connectionStatus === 'idle' || isSkipped ? 'Test Connection' : connectionStatus === 'failed' ? 'Retry' : 'Test Again'}
        </Button>
      )}

      <label className={styles.skipRow}>
        <input
          type="checkbox"
          checked={isSkipped}
          onChange={(e) => onStatusChange(e.target.checked ? 'skipped' : 'idle')}
        />
        <span className={styles.skipLabel}>Skip connection test</span>
      </label>
    </div>
  );
}
