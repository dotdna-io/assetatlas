// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useCallback } from 'react';

const STORAGE_KEY = 'darkMode';
const BODY_CLASS = 'dark-mode';

function readPreference(): boolean {
  return localStorage.getItem(STORAGE_KEY) === 'true';
}

function applyToBody(isDark: boolean): void {
  if (isDark) {
    document.body.classList.add(BODY_CLASS);
  } else {
    document.body.classList.remove(BODY_CLASS);
  }
}

export function useDarkMode(): { isDarkMode: boolean; toggle: () => void } {
  const [isDarkMode, setIsDarkMode] = useState(readPreference);

  // Apply on mount and when state changes
  useEffect(() => {
    applyToBody(isDarkMode);
  }, [isDarkMode]);

  // Cross-tab sync via storage event
  useEffect(() => {
    function handleStorage(e: StorageEvent) {
      if (e.key === STORAGE_KEY) {
        const newValue = e.newValue === 'true';
        setIsDarkMode(newValue);
      }
    }

    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  const toggle = useCallback(() => {
    setIsDarkMode((prev) => {
      const next = !prev;
      localStorage.setItem(STORAGE_KEY, String(next));
      return next;
    });
  }, []);

  return { isDarkMode, toggle };
}
