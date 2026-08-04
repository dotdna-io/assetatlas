// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import type { ExplorerFieldsResponse } from '@/api/assets';
import type { FieldCatalogEntry } from '@/types/field-id';
import { ASSET_LABEL_COLUMN_KEY } from '@/constants/assetTableColumns';
import { ColumnSelector } from '../ColumnSelector';

function entry(partial: Pick<FieldCatalogEntry, 'display_name' | 'encoded_key' | 'category'>): FieldCatalogEntry {
  return {
    field_id: { kind: 'static', name: partial.display_name },
    data_type: 'string',
    operator_specs: [],
    is_array: false,
    is_grouped: false,
    filter_only: false,
    member_sources: null,
    ...partial,
  };
}

const FIELDS: ExplorerFieldsResponse = {
  entries: [
    entry({
      display_name: 'Asset Label',
      encoded_key: ASSET_LABEL_COLUMN_KEY,
      category: 'virtual',
    }),
    entry({ display_name: 'Hostname', encoded_key: 'static:Hostname', category: 'target' }),
    entry({ display_name: 'Model', encoded_key: 'static:Model', category: 'target' }),
  ],
};

function renderSelector(onApply = vi.fn()) {
  const props = {
    isOpen: true,
    onClose: vi.fn(),
    fields: FIELDS,
    visibleColumns: {
      [ASSET_LABEL_COLUMN_KEY]: true,
      'static:Hostname': true,
      'static:Model': true,
    },
    onApply,
  };
  render(<ColumnSelector {...props} />);
  return props;
}

function checkboxOf(name: string): HTMLInputElement {
  const label = screen.getByText(name);
  const input = label.querySelector('input');
  if (!input) throw new Error(`no checkbox found for ${name}`);
  return input;
}

// The pinned Asset Label column can never be hidden (#1153): it renders
// checked + disabled with a hint, while every other field stays toggleable.
describe('ColumnSelector pinned Asset Label field', () => {
  it('shows the pinned field checked and disabled, with the hint', () => {
    renderSelector();
    const pin = checkboxOf('Asset Label');
    expect(pin.checked).toBe(true);
    expect(pin.disabled).toBe(true);
    expect(screen.getByText('Always first')).toBeInTheDocument();

    const hostname = checkboxOf('Hostname');
    expect(hostname.checked).toBe(true);
    expect(hostname.disabled).toBe(false);
  });

  it('apply keeps the pinned field visible even after other fields are hidden', () => {
    const onApply = vi.fn();
    renderSelector(onApply);

    fireEvent.click(checkboxOf('Hostname'));
    fireEvent.click(checkboxOf('Model'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({
        [ASSET_LABEL_COLUMN_KEY]: true,
        'static:Hostname': false,
        'static:Model': false,
      }),
    );
  });

  it('reset restores the shared default set with the pin visible', () => {
    const onApply = vi.fn();
    renderSelector(onApply);

    fireEvent.click(checkboxOf('Model'));
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

    const applied = onApply.mock.calls[0][0] as Record<string, boolean>;
    expect(applied[ASSET_LABEL_COLUMN_KEY]).toBe(true);
    expect(applied['static:Model']).toBe(true);
  });
});
