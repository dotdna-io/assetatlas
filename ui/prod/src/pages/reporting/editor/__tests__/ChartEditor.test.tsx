// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DarkModeProvider } from '@/hooks';
import { ChartEditor } from '../ChartEditor';
import type { ChartConfig } from '../../grid/ChartCard';

// The table editor's live preview fetches assets via react-query — irrelevant
// to these tests, so stub the component while keeping the module's other
// exports (ChartEditor imports DEFAULT_COLUMNS from it).
vi.mock('../../grid/ReportTable', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ReportTable: () => null,
}));

// A fresh donut card with no saved queryConfig — the state right after the
// user picks a chart type, before selecting any dimension.
const UNCONFIGURED_DONUT: ChartConfig = {
  id: 1,
  chartId: 'a7f3e8d1-4c2b-4f5a-9e1d-8b3c7a5e9f2d',
  className: 'DotDNAAnimatedDonutModern',
  name: 'Donut Chart',
  size: '2x2',
  allowedSizes: ['2x2', '3x2'],
  gridRow: 0,
  gridColumn: 0,
  defaultOptions: {},
};

describe('ChartEditor preview before configuration', () => {
  it('shows only the placeholder — no chart mounts until data exists', () => {
    render(
      <DarkModeProvider>
        <ChartEditor
          isOpen
          onClose={() => {}}
          chart={UNCONFIGURED_DONUT}
          assetFields={[]}
          onSave={() => {}}
        />
      </DarkModeProvider>,
    );

    expect(screen.getByText('Configure the chart to see a preview')).toBeTruthy();
    // Mounting the chart with no data paints ghost content behind the
    // placeholder (e.g. the donut's "0 TOTAL" center text) — the chart
    // container must not exist at all until a preview result arrives.
    expect(document.querySelector('[id^="dotdna-"]')).toBeNull();
  });
});

const DATA_TABLE: ChartConfig = {
  id: 2,
  chartId: 'e0a1b2c3-4d5e-6f7a-8b9c-0d1e2f3a4b5c',
  className: 'DotDNADataTable',
  name: 'Data Table',
  size: '6x4',
  allowedSizes: ['6x4'],
  gridRow: 0,
  gridColumn: 0,
  defaultOptions: {},
};

const TABLE_FIELDS = [
  {
    name: 'Hostname',
    value: 'static:Hostname',
    field_id: { kind: 'static', name: 'Hostname' } as const,
    type: 'string',
    category: 'Core',
  },
  {
    name: 'IP Address',
    value: 'static:IP%20Address',
    field_id: { kind: 'static', name: 'IP Address' } as const,
    type: 'string',
    category: 'Core',
  },
  {
    name: 'Acesurgical Entra - deviceId',
    value: 'ext:Acesurgical%20Entra:deviceId',
    field_id: {
      kind: 'extended_attribute',
      source: 'Acesurgical Entra',
      attribute: 'deviceId',
    } as const,
    type: 'string',
    category: 'Extended Attributes',
  },
];

describe('ChartEditor data table column search', () => {
  function renderTableEditor() {
    // DataSourceBadge (source group headers) resolves logos via react-query.
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <DarkModeProvider>
          <ChartEditor
            isOpen
            onClose={() => {}}
            chart={DATA_TABLE}
            assetFields={TABLE_FIELDS}
            onSave={() => {}}
          />
        </DarkModeProvider>
      </QueryClientProvider>,
    );
  }

  it('filters the visible-columns list by field name, hiding emptied source groups', () => {
    renderTableEditor();

    // All fields and both source groups start visible.
    expect(screen.getByText('Hostname')).toBeTruthy();
    expect(screen.getByText('Acesurgical Entra - deviceId')).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText('Search fields...'), {
      target: { value: 'host' },
    });

    expect(screen.getByText('Hostname')).toBeTruthy();
    expect(screen.queryByText('IP Address')).toBeNull();
    expect(screen.queryByText('Acesurgical Entra - deviceId')).toBeNull();
    // The Acesurgical group has no matches left — its header goes too.
    expect(screen.queryByText('Acesurgical Entra')).toBeNull();
  });

  it('starts a new table with the default columns visibly checked', () => {
    renderTableEditor();

    // ReportTable renders DEFAULT_COLUMNS when nothing is selected — the
    // picker must show that implicit selection as checked boxes, not start
    // blank while the preview displays eight columns.
    const checkboxOf = (name: string) =>
      screen.getByText(name).querySelector('input') as HTMLInputElement;

    expect(checkboxOf('Hostname').checked).toBe(true);
    expect(checkboxOf('IP Address').checked).toBe(true);
    expect(checkboxOf('Acesurgical Entra - deviceId').checked).toBe(false);
  });

  it('clearing the search restores every field', () => {
    renderTableEditor();
    const input = screen.getByPlaceholderText('Search fields...');

    fireEvent.change(input, { target: { value: 'device' } });
    expect(screen.queryByText('Hostname')).toBeNull();

    fireEvent.change(input, { target: { value: '' } });
    expect(screen.getByText('Hostname')).toBeTruthy();
    expect(screen.getByText('IP Address')).toBeTruthy();
    expect(screen.getByText('Acesurgical Entra - deviceId')).toBeTruthy();
  });
});
