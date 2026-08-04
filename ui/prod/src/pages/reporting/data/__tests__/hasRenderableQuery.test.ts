// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { hasRenderableQuery } from '../chartDataPipeline';

describe('hasRenderableQuery', () => {
  it('true for history_trend with no select slots', () => {
    expect(hasRenderableQuery({ queryType: 'history_trend', select: {}, options: { interval: 'day' } })).toBe(true);
  });
  it('true when a dimension is set', () => {
    expect(hasRenderableQuery({ queryType: 'distribution', select: { dimension: 'static:Category' } })).toBe(true);
  });
  it('false for an empty distribution config', () => {
    expect(hasRenderableQuery({ queryType: 'distribution', select: {} })).toBe(false);
  });
});
