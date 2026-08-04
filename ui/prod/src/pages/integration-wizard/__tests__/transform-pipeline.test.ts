// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { applyTransformPipeline } from '../transform-pipeline';

// The current_timestamp op lets timestamp-less sources stamp a field (e.g.
// last_time / "Last Seen") with the collection time. The wizard preview must
// show that value so users see what the transform will produce.
describe('applyTransformPipeline — current_timestamp', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-06-28T12:00:00.000Z'));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('emits the current time as an ISO-8601 string', () => {
    const { finalResult } = applyTransformPipeline(
      [{ id: 'op1', type: 'current_timestamp' }],
      {},
    );
    expect(finalResult).toBe('2026-06-28T12:00:00.000Z');
  });

  it('ignores the source record (no field dependency)', () => {
    const { finalResult } = applyTransformPipeline(
      [{ id: 'op1', type: 'current_timestamp' }],
      { unrelated: 'value' },
    );
    expect(finalResult).toBe('2026-06-28T12:00:00.000Z');
  });

  it('records a pipeline step for the operation', () => {
    const { steps } = applyTransformPipeline(
      [{ id: 'op1', type: 'current_timestamp' }],
      {},
    );
    expect(steps).toHaveLength(1);
    expect(steps[0].result).toBe('2026-06-28T12:00:00.000Z');
  });
});
