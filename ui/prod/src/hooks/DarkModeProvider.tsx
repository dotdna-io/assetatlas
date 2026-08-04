// SPDX-License-Identifier: AGPL-3.0-or-later
import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import type { ReactNode } from 'react';

const STORAGE_KEY = 'darkMode';
const BODY_CLASS = 'dark-mode';

interface DarkModeContextValue {
  isDarkMode: boolean;
  toggle: () => void;
}

const DarkModeContext = createContext<DarkModeContextValue | null>(null);

export function useDarkModeContext(): DarkModeContextValue {
  const ctx = useContext(DarkModeContext);
  if (ctx === null) {
    throw new Error('useDarkModeContext must be used within a DarkModeProvider');
  }
  return ctx;
}

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

interface DarkModeProviderProps {
  children: ReactNode;
}

export function DarkModeProvider({ children }: DarkModeProviderProps) {
  const [isDarkMode, setIsDarkMode] = useState(readPreference);

  // Apply body class on mount and state change
  useEffect(() => {
    applyToBody(isDarkMode);
  }, [isDarkMode]);

  // Cross-tab sync via storage event (including legacy pages)
  useEffect(() => {
    function handleStorage(e: StorageEvent) {
      if (e.key === STORAGE_KEY) {
        const newValue = e.newValue === 'true';
        // Apply the class synchronously before triggering the React
        // state update — otherwise descendants reading
        // `document.body.classList` from inside their `[isDarkMode]`
        // effects will see the stale class (child effects run before
        // the provider's class-applying effect).
        applyToBody(newValue);
        setIsDarkMode(newValue);
      }
    }

    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  const toggle = useCallback(() => {
    setIsDarkMode((prev) => {
      const next = !prev;
      // See comment in storage handler — descendants (e.g. DotDNAChart's
      // dark-mode effect that re-renders the gauge by reading the body
      // class) fire before the provider's effect, so the class must be
      // applied here, before the state update propagates.
      applyToBody(next);
      localStorage.setItem(STORAGE_KEY, String(next));
      return next;
    });
  }, []);

  return (
    <DarkModeContext.Provider value={{ isDarkMode, toggle }}>
      {children}
    </DarkModeContext.Provider>
  );
}
