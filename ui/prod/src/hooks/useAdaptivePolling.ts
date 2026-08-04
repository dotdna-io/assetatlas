// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useCallback, useRef, useEffect } from 'react';

/**
 * Returns a polling interval that drops to 1s after triggerBurst(),
 * then reverts to baseInterval after burstDuration ms.
 */
export function useAdaptivePolling(baseInterval = 5000, burstDuration = 30000) {
  const [interval, setInterval_] = useState(baseInterval);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const triggerBurst = useCallback(() => {
    setInterval_(1000);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setInterval_(baseInterval), burstDuration);
  }, [baseInterval, burstDuration]);

  useEffect(() => {
    return () => clearTimeout(timerRef.current);
  }, []);

  return { interval, triggerBurst } as const;
}
