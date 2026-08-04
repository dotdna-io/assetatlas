// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { getChartQuestions, generateChartQuery } from '../chartQuestions';

const CHRONICLE_BAR = 'a1b2c3d4-chr1-4e5f-9a8b-vertical-bar01';
const VERTICAL_BAR = 'a9b8e6c7-8d5f-4a9b-9c7e-8f9d6e7c5b4a';

describe('Chronicle Bar diverges from Vertical Bar by chartId', () => {
  it('Chronicle Bar uses the historical template', () => {
    const c = getChartQuestions('DotDNACleanSegmentedVerticalBar', CHRONICLE_BAR);
    expect(c!.template).toBe('trend');
    const q = generateChartQuery('DotDNACleanSegmentedVerticalBar', { metric: 'count', timeRange: '30d', interval: 'day' }, CHRONICLE_BAR);
    expect(q!.queryType).toBe('history_trend');
  });
  it('Vertical Bar stays live', () => {
    const c = getChartQuestions('DotDNACleanSegmentedVerticalBar', VERTICAL_BAR);
    expect(c!.template).toBe('barChart');
  });
  it('no chartId → className default (live bar)', () => {
    const c = getChartQuestions('DotDNACleanSegmentedVerticalBar');
    expect(c!.template).toBe('barChart');
  });
});
