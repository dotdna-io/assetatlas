// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { UploadModal } from '../UploadModal';
import { apiClient } from '@/api/client';
import type { FieldsResponse } from '@/api/tag-rules';

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

// /tag-rules/fields catalog with a core static field, a data-source-specific
// integration-group field, and a tag field (which must NOT be offered as an
// enrichment match key).
const FIELDS_RESPONSE: FieldsResponse = {
  entries: [
    {
      display_name: 'Hostname',
      field_id: { kind: 'static', name: 'Hostname' },
      encoded_key: 'static:Hostname',
      category: 'target',
      data_type: 'string',
      is_array: false,
      is_grouped: false,
      member_sources: null,
    },
    {
      display_name: 'Entra - building',
      field_id: { kind: 'integration_group', group: 'Entra', attribute: 'building' },
      encoded_key: 'group:Entra:building',
      category: 'extended_attributes',
      data_type: 'string',
      is_array: false,
      is_grouped: true,
      member_sources: ['Entra A', 'Entra B'],
    },
    {
      display_name: 'Tags - prod',
      field_id: { kind: 'tag', name: 'prod' },
      encoded_key: 'tag:prod',
      category: 'tags',
      data_type: 'boolean',
      is_array: false,
      is_grouped: false,
      member_sources: null,
    },
  ],
};

function renderModal() {
  const onClose = vi.fn();
  const qc = makeQueryClient();
  // Pre-populate the field catalog so the dropdown renders synchronously.
  qc.setQueryData(['tag-rules', 'fields'], FIELDS_RESPONSE);

  render(
    <QueryClientProvider client={qc}>
      <UploadModal isOpen onClose={onClose} />
    </QueryClientProvider>,
  );

  return { onClose, qc };
}

async function fillNameAndCsv(name: string, csvText: string, fileName: string) {
  fireEvent.change(screen.getByPlaceholderText('e.g. Location Mapping'), {
    target: { value: name },
  });
  const csv = new File([csvText], fileName, { type: 'text/csv' });
  const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(fileInput, { target: { files: [csv] } });
  await waitFor(() =>
    expect(screen.getByText('Match Column (CSV)')).toBeInTheDocument(),
  );
}

describe('UploadModal', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  // Regression guard for the upload 400: the backend route
  // (src/api/blueprints/enrichment/enrichment_routes.py) reads the CSV column
  // names from the `match_col` / `enrich_col` form fields. Sending
  // `match_column` / `enrich_column` makes those required fields arrive empty,
  // so the route raises "Missing required fields: match_col, enrich_col" (400).
  it('uploads with the form field names the backend reads (match_col/enrich_col)', async () => {
    const uploadSpy = vi.spyOn(apiClient, 'upload').mockResolvedValue({
      dataset_id: 'ds-1',
      row_count: 1,
      preview: [],
      columns: ['host', 'site'],
    });

    renderModal();
    await fillNameAndCsv('Location Mapping', 'host,site\nsrv-01,NYC\n', 'loc.csv');

    // Pick the match asset field via the SearchableDropdown.
    fireEvent.click(screen.getByRole('button', { name: 'Match asset field' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Hostname' }));

    // Submit
    fireEvent.click(screen.getByRole('button', { name: /Upload & Preview/i }));

    await waitFor(() => expect(uploadSpy).toHaveBeenCalledTimes(1));

    const [url, sentForm] = uploadSpy.mock.calls[0];
    expect(url).toBe('/enrichment/upload');
    const fd = sentForm as FormData;

    // The contract the backend actually reads.
    expect(fd.get('match_col')).toBe('host');
    expect(fd.get('enrich_col')).toBe('site');
    // The buggy names must NOT be what we send.
    expect(fd.get('match_column')).toBeNull();
    expect(fd.get('enrich_column')).toBeNull();

    // Sanity on the fields that were already correct.
    expect(fd.get('name')).toBe('Location Mapping');
    // match_field is now the canonical encoded field id the backend accepts.
    expect(fd.get('match_field')).toBe('static:Hostname');
    expect(fd.get('target_field')).toBe('site');
    expect(fd.get('match_type')).toBe('exact');
  });

  // The bug: selecting an integration-group field used to submit the bare kind
  // literal "integration_group", which the backend rejected (400). It must now
  // send the canonical encoded field id.
  it('sends the canonical encoded id for an integration-group field, not the bare kind', async () => {
    const uploadSpy = vi.spyOn(apiClient, 'upload').mockResolvedValue({
      dataset_id: 'ds-2',
      row_count: 1,
      preview: [],
      columns: ['site', 'owner'],
    });

    renderModal();
    await fillNameAndCsv('Site owners', 'site,owner\nHQ,alice\n', 'site.csv');

    fireEvent.click(screen.getByRole('button', { name: 'Match asset field' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Entra - building' }));

    fireEvent.click(screen.getByRole('button', { name: /Upload & Preview/i }));
    await waitFor(() => expect(uploadSpy).toHaveBeenCalledTimes(1));

    const fd = uploadSpy.mock.calls[0][1] as FormData;
    expect(fd.get('match_field')).toBe('group:Entra:building');
    expect(fd.get('match_field')).not.toBe('integration_group');
  });

  // Enrichment matches on core/data-source fields; tag/posture/virtual fields
  // are not meaningful match keys and must not be offered.
  it('does not offer tag fields as a match field', async () => {
    renderModal();
    fireEvent.click(screen.getByRole('button', { name: 'Match asset field' }));

    expect(await screen.findByRole('option', { name: 'Hostname' })).toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: 'Tags - prod' }),
    ).not.toBeInTheDocument();
  });
});
