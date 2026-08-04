// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import React from 'react';

import { ChatProvider, useChatUI } from '../ChatProvider';

function wrapper({ children }: { children: React.ReactNode }) {
  return <ChatProvider>{children}</ChatProvider>;
}

beforeEach(() => {
  localStorage.clear();
});

describe('ChatProvider', () => {
  it('defaults when no persisted state', () => {
    const { result } = renderHook(() => useChatUI(), { wrapper });
    expect(result.current.open).toBe(false);
    expect(result.current.currentSessionId).toBeNull();
  });

  it('persists updates to localStorage', () => {
    const { result } = renderHook(() => useChatUI(), { wrapper });
    act(() => {
      result.current.setOpen(true);
      result.current.setCurrentSessionId('s-123');
    });
    const raw = localStorage.getItem('chat_ui_state');
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw!);
    expect(parsed.open).toBe(true);
    expect(parsed.currentSessionId).toBe('s-123');
  });

  it('hydrates from localStorage', () => {
    localStorage.setItem(
      'chat_ui_state',
      JSON.stringify({ open: true, currentSessionId: 's-456' }),
    );
    const { result } = renderHook(() => useChatUI(), { wrapper });
    expect(result.current.open).toBe(true);
    expect(result.current.currentSessionId).toBe('s-456');
  });

  it('falls back to defaults on corrupt storage', () => {
    localStorage.setItem('chat_ui_state', '{not-json');
    const { result } = renderHook(() => useChatUI(), { wrapper });
    expect(result.current.open).toBe(false);
  });

  it('throws when used outside provider', () => {
    expect(() => renderHook(() => useChatUI())).toThrow(
      /must be used within a ChatProvider/,
    );
  });

  it('drops the active session when reopened after the resume window', () => {
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(1_000_000);
    const { result } = renderHook(() => useChatUI(), { wrapper });
    act(() => {
      result.current.setOpen(true);
      result.current.setCurrentSessionId('s-1');
    });
    act(() => { result.current.setOpen(false); });   // closedAt = 1_000_000
    nowSpy.mockReturnValue(1_000_000 + 61_000);       // reopen 61s later
    act(() => { result.current.setOpen(true); });
    expect(result.current.currentSessionId).toBeNull();
    nowSpy.mockRestore();
  });

  it('resumes the active session when reopened within the resume window', () => {
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(1_000_000);
    const { result } = renderHook(() => useChatUI(), { wrapper });
    act(() => {
      result.current.setOpen(true);
      result.current.setCurrentSessionId('s-1');
    });
    act(() => { result.current.setOpen(false); });   // closedAt = 1_000_000
    nowSpy.mockReturnValue(1_000_000 + 30_000);       // reopen 30s later
    act(() => { result.current.setOpen(true); });
    expect(result.current.currentSessionId).toBe('s-1');
    nowSpy.mockRestore();
  });
});
