// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from 'react';

/**
 * Returns `value`, delaying each update until it has stopped changing for
 * `delayMs`. The initial render is seeded to the current `value` (no blank
 * first paint) — only subsequent changes are delayed. Same 500ms cadence as
 * the standard chart preview's debounce (`useChartPreview.ts`).
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}
