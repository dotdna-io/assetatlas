// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConnectionTestStep, MAX_UPLOAD_BYTES } from '@/pages/integration-wizard/ConnectionTestStep';
import type { WizardFormData } from '@/pages/integration-wizard/wizard-state';
import { integrationsApi } from '@/api/integrations';
vi.mock('@/api/integrations');

function makeFormData(overrides: Partial<WizardFormData> = {}): WizardFormData {
  return {
    integration_type: 'palo-alto-networks',
    source_method: 'sentinelone_siem',
    name: '',
    description: '',
    category: null,
    enabled: true,
    logo: null,
    config: {
      url: 'https://xdr.eu1.sentinelone.net',
      api_token: 'token',
      powerquery: "source='scalyr' | limit 1",
    },
    field_mapping: [],
    transformations: [],
    preserve_fields: [],
    data_filters: [],
    schedule: { cron: '', timezone: 'UTC', enabled: false },
    preview_session_id: null,
    sampleData: null,
    sourceFields: null,
    ...overrides,
  };
}

function renderStep(formData: WizardFormData) {
  return render(
    <ConnectionTestStep
      formData={formData}
      onUpdate={vi.fn()}
      integrationId={null}
      mode="create"
      connectionStatus="idle"
      onStatusChange={vi.fn()}
    />,
  );
}

// Click "Test Connection" and wait for handleTest to fully settle. delete() is
// the last step of its finally block, so once it fires the async tail (sample
// fetch + setFetchingSamples) has flushed inside act() — nothing leaks into the
// next test.
async function clickTestAndSettle() {
  fireEvent.click(screen.getByRole('button', { name: /test connection/i }));
  await waitFor(() => expect(integrationsApi.delete).toHaveBeenCalled());
}

describe('ConnectionTestStep — via-SIEM routing', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (integrationsApi.create as any) = vi
      .fn()
      .mockResolvedValue({ success: true, integration: { id: 'temp-1' } });
    (integrationsApi.testConnection as any) = vi
      .fn()
      .mockResolvedValue({ success: true, message: 'ok' });
    (integrationsApi.getSampleData as any) = vi
      .fn()
      .mockResolvedValue({ records: [] });
    (integrationsApi.delete as any) = vi.fn().mockResolvedValue({ success: true });
  });

  // Regression for the 400 on "Test" when adding a technology via a SIEM
  // transport: the temp draft must carry source_method, otherwise the backend
  // defaults it to 'direct' and effective_connector_type resolves to the
  // technology ('palo-alto-networks') — which has no connector — instead of the
  // transport ('sentinelone_siem'), raising "Unknown integration_type".
  it('sends source_method on the temp draft so the test routes to the transport connector', async () => {
    renderStep(makeFormData());

    await clickTestAndSettle();
    expect(integrationsApi.create).toHaveBeenCalledWith(
      expect.objectContaining({
        integration_type: 'palo-alto-networks',
        source_method: 'sentinelone_siem',
      }),
    );
  });

  it('preserves a direct source_method for non-SIEM integrations', async () => {
    renderStep(
      makeFormData({ integration_type: 'sentinelone_siem', source_method: 'direct' }),
    );

    await clickTestAndSettle();
    expect(integrationsApi.create).toHaveBeenCalledWith(
      expect.objectContaining({
        integration_type: 'sentinelone_siem',
        source_method: 'direct',
      }),
    );
  });
});

// ---------------------------------------------------------------------------
// #1133 — manual upload persisted no file at all.
//
// Reading the file only ever produced sampleData/sourceFields for the mapping
// UI, and the content was then dropped. So the wizard looked entirely healthy
// — headers discovered, mapping populated, integration saved — while the saved
// config held `{"_file": "{}", "_fileName": "sample.csv"}` and EVERY run failed
// with "No file data provided". `_file` serialises to {} because a browser File
// has no enumerable own properties.
//
// These assert on what the CONNECTOR reads. A test that only checked the step
// reached the mapping stage passed throughout the bug.
// ---------------------------------------------------------------------------

function makeCsvFile(name = 'sample-assets.csv', body = 'hostname,ip\nweb-1,10.0.0.1\n') {
  const file = new File([body], name, { type: 'text/csv' });
  // jsdom's File has no working text/arrayBuffer plumbing for FileReader in
  // every version; readAsText works, so nothing else is stubbed here.
  return file;
}

function renderManualStep(config: Record<string, any>, onUpdate: any) {
  return render(
    <ConnectionTestStep
      formData={makeFormData({ integration_type: 'manual_upload', config })}
      onUpdate={onUpdate}
      integrationId={null}
      mode="create"
      connectionStatus="idle"
      onStatusChange={vi.fn()}
    />,
  );
}

describe('ConnectionTestStep — manual upload (#1133)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('writes the file content into config so the connector can read it', async () => {
    const onUpdate = vi.fn();
    const file = makeCsvFile();
    renderManualStep({ _file: file, _fileName: file.name }, onUpdate);

    fireEvent.click(screen.getByRole('button', { name: /process file/i }));

    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    const update = onUpdate.mock.calls.at(-1)![0];

    expect(update.config.file_data).toContain('web-1');
    expect(update.config.file_format).toBe('csv');
    expect(update.config.file_name).toBe('sample-assets.csv');
    // The mapping UI must still get what it needs.
    expect(update.sourceFields).toEqual(['hostname', 'ip']);
  });

  it('records the format from the extension rather than defaulting to csv', async () => {
    const onUpdate = vi.fn();
    const file = new File(['[{"a":1}]'], 'assets.json', { type: 'application/json' });
    renderManualStep({ _file: file, _fileName: file.name }, onUpdate);

    fireEvent.click(screen.getByRole('button', { name: /process file/i }));

    await waitFor(() => expect(onUpdate).toHaveBeenCalled());
    expect(onUpdate.mock.calls.at(-1)![0].config.file_format).toBe('json');
  });

  it('shows the file name of an already-saved integration', () => {
    // Edit mode: `_fileName` is stripped before save, so the saved `file_name`
    // is all that is left. Without the fallback the step claims no file.
    renderManualStep({ file_name: 'previous.csv', file_data: 'a,b\n1,2\n' }, vi.fn());
    expect(screen.getByText(/previous\.csv/)).toBeInTheDocument();
  });

  it('refuses a file too large to inline into the config row', async () => {
    const onStatusChange = vi.fn();
    const onUpdate = vi.fn();
    const big = makeCsvFile('huge.csv', 'x');
    Object.defineProperty(big, 'size', { value: MAX_UPLOAD_BYTES + 1 });
    render(
      <ConnectionTestStep
        formData={makeFormData({ integration_type: 'manual_upload', config: { _file: big, _fileName: big.name } })}
        onUpdate={onUpdate}
        integrationId={null}
        mode="create"
        connectionStatus="idle"
        onStatusChange={onStatusChange}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /process file/i }));

    expect(onStatusChange).toHaveBeenCalledWith('failed');
    expect(onUpdate).not.toHaveBeenCalled();
  });
});
