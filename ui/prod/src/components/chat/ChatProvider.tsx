// SPDX-License-Identifier: AGPL-3.0-or-later
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';

export interface ChatUIState {
  open: boolean;
  setOpen: (v: boolean) => void;
  currentSessionId: string | null;
  setCurrentSessionId: (id: string | null) => void;
}

interface PersistedState {
  open?: boolean;
  currentSessionId?: string | null;
  closedAt?: number | null;
}

const Ctx = createContext<ChatUIState | null>(null);
const STORAGE_KEY = 'chat_ui_state';
// Reopening within this window resumes the conversation you had open; reopen
// after it and the active session is dropped so the panel lands on the history
// list instead of a stale conversation.
const RESUME_WINDOW_MS = 60_000;

function readPersisted(): PersistedState {
  if (typeof localStorage === 'undefined') return {};
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

export function ChatProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpenState] = useState<boolean>(
    () => readPersisted().open ?? false,
  );
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(
    () => readPersisted().currentSessionId ?? null,
  );
  const [closedAt, setClosedAt] = useState<number | null>(
    () => readPersisted().closedAt ?? null,
  );

  const setOpen = useCallback(
    (v: boolean) => {
      if (v === open) return;
      if (v) {
        // Reopening: if the panel sat closed past the resume window, drop the
        // active session so we land on the history list rather than resuming a
        // stale conversation.
        if (closedAt !== null && Date.now() - closedAt > RESUME_WINDOW_MS) {
          setCurrentSessionId(null);
        }
        setClosedAt(null);
      } else {
        // Closing: start the resume-window clock.
        setClosedAt(Date.now());
      }
      setOpenState(v);
    },
    [open, closedAt],
  );

  useEffect(() => {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ open, currentSessionId, closedAt }),
      );
    } catch {
      // storage quota / disabled — ignore
    }
  }, [open, currentSessionId, closedAt]);

  const value = useMemo<ChatUIState>(
    () => ({ open, setOpen, currentSessionId, setCurrentSessionId }),
    [open, setOpen, currentSessionId],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useChatUI(): ChatUIState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useChatUI must be used within a ChatProvider');
  return v;
}
