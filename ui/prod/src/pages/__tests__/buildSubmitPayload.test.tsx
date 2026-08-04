// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { buildSubmitPayload } from '@/pages/IntegrationWizardPage';
import type { WizardFormData } from '@/pages/integration-wizard/wizard-state';

function makeFormData(overrides: Partial<WizardFormData> = {}): WizardFormData {
  return {
    integration_type: 'palo-alto-networks',
    source_method: 'sentinelone_siem',
    name: 'PAN via SIEM',
    description: '',
    category: 'Firewall',
    enabled: true,
    logo: null,
    config: { url: 'https://xdr.eu1.sentinelone.net', api_token: 'token' },
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

describe('buildSubmitPayload', () => {
  // Regression: the saved integration must carry source_method. Without it the
  // backend defaults to 'direct', effective_connector_type resolves to the
  // technology (e.g. 'palo-alto-networks') instead of the transport, and every
  // scheduled sync fails with "Unknown integration_type".
  it('includes source_method so saved via-SIEM integrations route to the transport', () => {
    const payload = buildSubmitPayload(makeFormData(), 'create');
    expect(payload.source_method).toBe('sentinelone_siem');
    expect(payload.integration_type).toBe('palo-alto-networks');
  });

  it('preserves a direct source_method for non-SIEM integrations', () => {
    const payload = buildSubmitPayload(
      makeFormData({ integration_type: 'sentinelone_siem', source_method: 'direct' }),
      'create',
    );
    expect(payload.source_method).toBe('direct');
  });

  // The category key differs by mode (create uses data_source_category, edit
  // uses category) — guard that the extraction kept that branch intact.
  it('uses data_source_category on create and category on edit', () => {
    const create = buildSubmitPayload(makeFormData(), 'create') as Record<string, unknown>;
    const edit = buildSubmitPayload(makeFormData(), 'edit') as Record<string, unknown>;
    expect(create.data_source_category).toBe('Firewall');
    expect(create.category).toBeUndefined();
    expect(edit.category).toBe('Firewall');
    expect(edit.data_source_category).toBeUndefined();
  });

  // Transformations are reshaped into the backend's { type, operations } form
  // with the wizard-only `id` stripped from each operation; data_filters pass
  // through verbatim.
  it('serializes transformations (stripping op ids) and passes data_filters through', () => {
    const payload = buildSubmitPayload(
      makeFormData({
        transformations: [
          { name: 'normalize', operations: [{ id: 'op-1', type: 'uppercase', field: 'hostname' }] },
        ],
        data_filters: [{ field: 'status', operator: 'eq', value: 'active' }],
      }),
      'create',
    ) as Record<string, unknown>;
    expect(payload.transformations).toEqual({
      normalize: { type: 'transform', operations: [{ type: 'uppercase', field: 'hostname' }] },
    });
    expect(payload.data_filters).toEqual([{ field: 'status', operator: 'eq', value: 'active' }]);
  });

  it('omits transformations and data_filters when empty', () => {
    const payload = buildSubmitPayload(makeFormData(), 'create') as Record<string, unknown>;
    expect(payload.transformations).toBeUndefined();
    expect(payload.data_filters).toBeUndefined();
  });

  // #809: DSS fields must be sent as a distinct preserve_fields array (the
  // authoritative source-of-truth for edit-mode reconstruction) AND still
  // remain {field:field} self-mappings in field_mapping so the asset-generation
  // pipeline — which reads field_mapping — sees no change.
  it('emits preserve_fields distinctly while still self-mapping them into field_mapping', () => {
    const payload = buildSubmitPayload(
      makeFormData({
        field_mapping: [{ source: 'device_name', target: 'hostname' }],
        preserve_fields: ['os', 'custom_tag'],
      }),
      'edit',
    ) as Record<string, unknown>;

    expect(payload.preserve_fields).toEqual(['os', 'custom_tag']);
    expect(payload.field_mapping).toMatchObject({
      hostname: 'device_name',
      os: 'os',
      custom_tag: 'custom_tag',
    });
  });
});

// ---------------------------------------------------------------------------
// #1133 — `_`-prefixed wizard scratch keys must not be persisted.
//
// The wizard uses that prefix for browser-only values (the same convention
// extractFromObject applies during field discovery), but nothing enforced it on
// save. The manual-upload step put a browser File in `config._file`, which
// JSON-serialises to the literal `{}` because a File has no enumerable own
// properties — so integrations were stored with `{"_file": "{}"}` in their
// config. That is how a non-serializable value reached the database.
// ---------------------------------------------------------------------------

describe('buildSubmitPayload — internal config keys', () => {
  it('drops _-prefixed scratch keys from the saved config', () => {
    const payload = buildSubmitPayload(
      makeFormData({
        config: {
          _file: new File(['a,b\n1,2\n'], 'sample.csv'),
          _fileName: 'sample.csv',
          file_data: 'a,b\n1,2\n',
          file_format: 'csv',
          file_name: 'sample.csv',
        },
      }),
      'create',
    );

    expect(payload.config).not.toHaveProperty('_file');
    expect(payload.config).not.toHaveProperty('_fileName');
    // Nothing survives JSON in the File's place — assert the whole config is
    // round-trippable, which is the property that actually matters.
    expect(JSON.parse(JSON.stringify(payload.config))).toEqual(payload.config);
  });

  it('keeps the real connector fields, which is the point of the upload', () => {
    const payload = buildSubmitPayload(
      makeFormData({
        config: { _file: {}, file_data: 'a,b\n1,2\n', file_format: 'csv', file_name: 'sample.csv' },
      }),
      'create',
    );
    expect(payload.config.file_data).toBe('a,b\n1,2\n');
    expect(payload.config.file_format).toBe('csv');
    expect(payload.config.file_name).toBe('sample.csv');
  });

  it('leaves ordinary connector configs untouched', () => {
    const payload = buildSubmitPayload(makeFormData(), 'create');
    expect(payload.config).toEqual({
      url: 'https://xdr.eu1.sentinelone.net',
      api_token: 'token',
    });
  });
});
