// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { wizardReducer, initialState } from '@/pages/integration-wizard/wizard-state';

function loadIntegration(
  integration: Record<string, any>,
  mode: 'edit' | 'clone' = 'edit',
) {
  return wizardReducer(initialState, {
    type: 'LOAD_INTEGRATION',
    payload: { integration, mode },
  });
}

describe('wizardReducer LOAD_INTEGRATION — data-source-specific reconstruction', () => {
  // Regression: DSS/preserve fields are persisted as self-mappings
  // ({field: field}) by buildFieldMappingPayload, and the backend has no
  // separate preserve_fields. On edit-mode load they must be pulled back into
  // preserve_fields — otherwise they land in field_mapping as an unrecognized
  // framework mapping: the source card highlights as mapped but renders no
  // "Data Source Specific" badge, and the right column doesn't show them.
  it('moves self-mapped non-framework fields into preserve_fields', () => {
    const state = loadIntegration({
      id: 'abc',
      integration_type: 'custom_http',
      status: 'enabled',
      field_mapping: {
        hostname: 'device_name',         // real framework mapping
        agent_version: 'agent_version',  // DSS self-mapping
        custom_tag: 'custom_tag',        // DSS self-mapping
      },
    });

    expect(state.formData.field_mapping).toContainEqual({
      source: 'device_name',
      target: 'hostname',
    });
    expect(state.formData.preserve_fields).toEqual(
      expect.arrayContaining(['agent_version', 'custom_tag']),
    );
    // DSS fields must NOT remain in field_mapping
    expect(
      state.formData.field_mapping.find((m) => m.target === 'agent_version'),
    ).toBeUndefined();
    expect(
      state.formData.field_mapping.find((m) => m.target === 'custom_tag'),
    ).toBeUndefined();
  });

  it('keeps a self-mapping onto a real framework field as a mapping', () => {
    // A source field literally named "hostname" mapped to the framework
    // "hostname" target is a genuine mapping, not a preserved field.
    const state = loadIntegration({
      id: 'abc',
      integration_type: 'custom_http',
      status: 'enabled',
      field_mapping: { hostname: 'hostname' },
    });

    expect(state.formData.field_mapping).toContainEqual({
      source: 'hostname',
      target: 'hostname',
    });
    expect(state.formData.preserve_fields).not.toContain('hostname');
  });

  it('still honours an explicit preserve_fields from the payload', () => {
    const state = loadIntegration({
      id: 'abc',
      integration_type: 'custom_http',
      status: 'enabled',
      field_mapping: { serial: 'serial' },
      preserve_fields: ['extra_field'],
    });

    expect(state.formData.preserve_fields).toEqual(
      expect.arrayContaining(['extra_field', 'serial']),
    );
  });

  it('classifies a framework-named field as DSS when preserve_fields says so (#809)', () => {
    // #809: a DSS field named exactly like a canonical framework field (os,
    // user, owner, model, location, ...) is persisted BOTH as a {field:field}
    // self-mapping AND — since the distinct-persistence fix — in the
    // authoritative preserve_fields array. preserve_fields wins: the field must
    // render as "Data Source Specific", NOT be reconstructed as a framework
    // mapping (badge "→ Operating System"). Without preserve_fields being
    // authoritative, the framework-name heuristic sends {os: os} to
    // field_mapping and the user's DSS intent is silently lost on edit.
    const state = loadIntegration({
      id: 'abc',
      integration_type: 'custom_http',
      status: 'enabled',
      field_mapping: { os: 'os', user: 'user', hostname: 'device_name' },
      preserve_fields: ['os', 'user'],
    });

    // Framework-named DSS fields land in preserve_fields...
    expect(state.formData.preserve_fields).toEqual(
      expect.arrayContaining(['os', 'user']),
    );
    // ...and must NOT be reconstructed as framework mappings.
    expect(
      state.formData.field_mapping.find((m) => m.target === 'os'),
    ).toBeUndefined();
    expect(
      state.formData.field_mapping.find((m) => m.target === 'user'),
    ).toBeUndefined();
    // A genuine framework mapping alongside them is untouched.
    expect(state.formData.field_mapping).toContainEqual({
      source: 'device_name',
      target: 'hostname',
    });
  });

  it('legacy fallback: a framework-named self-mapping with no preserve_fields stays a framework mapping', () => {
    // No backfill for rows saved before preserve_fields existed: with the array
    // empty/absent, {os: os} is indistinguishable from a genuine source→os
    // mapping, so the heuristic (unchanged) keeps it in field_mapping. This is
    // the deliberately-unchanged legacy behavior — the exact contrast to the
    // #809 test above where preserve_fields disambiguates it as DSS.
    const state = loadIntegration({
      id: 'abc',
      integration_type: 'custom_http',
      status: 'enabled',
      field_mapping: { os: 'os' },
      // preserve_fields intentionally omitted (legacy row)
    });

    expect(state.formData.field_mapping).toContainEqual({
      source: 'os',
      target: 'os',
    });
    expect(state.formData.preserve_fields).not.toContain('os');
  });

  it('does not drop a genuine non-self mapping whose target is also in preserve_fields', () => {
    // Defensive: an out-of-band/API/import write can leave field_mapping and
    // preserve_fields inconsistent — a real mapping raw_os -> os alongside
    // preserve_fields=['os']. The authoritative-skip must only remove the
    // {os:os} self-map that encodes a DSS field, never a genuine mapping, so
    // raw_os -> os survives edit-load instead of being silently lost on the
    // next save. (The wizard's own save never writes this shape; this guards
    // externally-created rows.)
    const state = loadIntegration({
      id: 'abc',
      integration_type: 'custom_http',
      status: 'enabled',
      field_mapping: { os: 'raw_os' }, // genuine mapping: source raw_os -> target os
      preserve_fields: ['os'],
    });

    expect(state.formData.field_mapping).toContainEqual({
      source: 'raw_os',
      target: 'os',
    });
  });
});
