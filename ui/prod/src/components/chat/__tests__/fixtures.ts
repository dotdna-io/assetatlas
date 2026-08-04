// SPDX-License-Identifier: AGPL-3.0-or-later
import type {
  DonutArtifact,
  StackedBarArtifact,
  TableArtifact,
} from '../chartArtifactTypes';


interface MakeDonutOverrides {
  title?: DonutArtifact['title'];
  series?: DonutArtifact['series'];
  meta?: Partial<DonutArtifact['meta']>;
}


/** Build a valid donut envelope for tests. ``series`` defaults to two
 *  slices summing to 8; ``meta.total`` derives from the series sum
 *  unless explicitly overridden. */
export function makeDonutArtifact(
  overrides: MakeDonutOverrides = {},
): DonutArtifact {
  const series = overrides.series ?? [
    { label: 'server', value: 5 },
    { label: 'workstation', value: 3 },
  ];
  const total = series.reduce((acc, s) => acc + s.value, 0);
  return {
    type: 'donut',
    title: overrides.title ?? 'Assets by Category',
    series,
    meta: {
      source_tool: 'aggregate_assets',
      total,
      other_count: 0,
      row_field: 'category',
      ...(overrides.meta ?? {}),
    },
  };
}


interface MakeStackedBarOverrides {
  title?: StackedBarArtifact['title'];
  series?: StackedBarArtifact['series'];
  meta?: Partial<StackedBarArtifact['meta']>;
}

export function makeStackedBarArtifact(
  overrides: MakeStackedBarOverrides = {},
): StackedBarArtifact {
  const series = overrides.series ?? [
    { x_label: 'server', stacks: [
      { label: 'win', value: 3 }, { label: 'linux', value: 2 },
    ]},
    { x_label: 'workstation', stacks: [
      { label: 'win', value: 4 }, { label: 'linux', value: 1 },
    ]},
  ];
  const total = series.reduce(
    (acc, bar) => acc + bar.stacks.reduce((a, s) => a + s.value, 0),
    0,
  );
  return {
    type: 'stacked_bar',
    title: overrides.title ?? 'Assets by Category × OS Normalized',
    series,
    meta: {
      source_tool: 'aggregate_assets',
      total,
      row_field: 'category',
      col_field: 'os_normalized',
      ...(overrides.meta ?? {}),
    },
  };
}


interface MakeTableOverrides {
  title?: TableArtifact['title'];
  columns?: TableArtifact['columns'];
  series?: TableArtifact['series'];
  meta?: Partial<TableArtifact['meta']>;
}

export function makeTableArtifact(
  overrides: MakeTableOverrides = {},
): TableArtifact {
  const columns = overrides.columns ?? [
    { key: 'hostname', label: 'Hostname' },
    { key: 'category', label: 'Category' },
  ];
  const series = overrides.series ?? [
    { hostname: ['h1.example'], category: 'server' },
    { hostname: ['h2.example'], category: 'workstation' },
  ];
  return {
    type: 'table',
    title: overrides.title ?? 'Assets (2)',
    columns,
    series,
    meta: {
      source_tool: 'list_assets',
      layout: 'list',
      total: 2,
      truncated: false,
      ...(overrides.meta ?? {}),
    } as TableArtifact['meta'],
  };
}
