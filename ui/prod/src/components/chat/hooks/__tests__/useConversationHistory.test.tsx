// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import { useConversationHistory } from '../useConversationHistory';
import * as chat from '../../../../api/chat';

vi.mock('../../../../api/chat', () => ({
  listSessions: vi.fn(),
  createSession: vi.fn(),
  renameSession: vi.fn(),
  deleteSession: vi.fn(),
  autotitleSession: vi.fn(),
}));

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

function wrapperWith(qc: QueryClient) {
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('useConversationHistory', () => {
  it('exposes sessions from listSessions', async () => {
    (chat.listSessions as any).mockResolvedValue({
      sessions: [{ id: 's1', title: 'First' }],
      total: 1,
      page: 1,
      per_page: 50,
    });
    const { result } = renderHook(() => useConversationHistory(), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.sessions).toHaveLength(1);
    expect(result.current.sessions[0].id).toBe('s1');
    expect(chat.listSessions).toHaveBeenCalledWith({ perPage: 50 });
  });

  it('invalidates sessions on rename', async () => {
    (chat.listSessions as any).mockResolvedValue({
      sessions: [], total: 0, page: 1, per_page: 50,
    });
    (chat.renameSession as any).mockResolvedValue({});

    const { result } = renderHook(() => useConversationHistory(), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.rename.mutateAsync({ id: 's1', title: 'new' });
    });

    expect(chat.renameSession).toHaveBeenCalledWith('s1', 'new');
  });

  it('invalidates sessions on remove', async () => {
    (chat.listSessions as any).mockResolvedValue({
      sessions: [], total: 0, page: 1, per_page: 50,
    });
    (chat.deleteSession as any).mockResolvedValue(undefined);
    const { result } = renderHook(() => useConversationHistory(), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => {
      await result.current.remove.mutateAsync('s1');
    });
    expect(chat.deleteSession).toHaveBeenCalledWith('s1');
  });

  it('create forwards title', async () => {
    (chat.listSessions as any).mockResolvedValue({
      sessions: [], total: 0, page: 1, per_page: 50,
    });
    (chat.createSession as any).mockResolvedValue({
      session_id: 'new-s', title: 'Hello', created_at: 'x',
    });
    const { result } = renderHook(() => useConversationHistory(), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    await act(async () => {
      await result.current.create.mutateAsync('Hello');
    });
    expect(chat.createSession).toHaveBeenCalledWith('Hello');
  });

  it('re-validates the session so a stale refetch cannot clobber a new title', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // Session-detail cache as it looks right after getSession on open: still the
    // default title, with the prior message already loaded.
    qc.setQueryData(['chat', 'session', 's1'], {
      session: {
        id: 's1', title: 'Untitled conversation',
        model: 'm', provider: 'p', created_at: 't', updated_at: 't',
      },
      messages: [{
        id: 'm0', seq: 0, role: 'user', content: 'hi',
        tool_calls: null, input_tokens: null, output_tokens: null, created_at: 't',
      }],
    });
    (chat.listSessions as any).mockResolvedValue({
      sessions: [{
        id: 's1', title: 'Untitled conversation',
        model: 'm', provider: 'p', created_at: 't', updated_at: 't',
      }],
      total: 1, page: 1, per_page: 50,
    });
    (chat.autotitleSession as any).mockResolvedValue({
      id: 's1', title: 'Asset Counts',
      model: 'm', provider: 'p', created_at: 't', updated_at: 't',
    });

    const { result } = renderHook(() => useConversationHistory(), {
      wrapper: wrapperWith(qc),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.autotitle.mutateAsync({ id: 's1', userText: 'hi' });
    });

    const detail = qc.getQueryData(['chat', 'session', 's1']) as any;
    // Optimistic patch shows the title instantly and keeps the messages.
    expect(detail.session.title).toBe('Asset Counts');
    expect(detail.messages).toHaveLength(1);
    // The session detail is invalidated (no observer here, so the flag sticks):
    // this scheduled re-read is what cancels the racing stale refetch.
    // In the real app useChatSession registers an active observer for this
    // query, so there the invalidation triggers an immediate refetch which
    // in turn hits the cancelRefetch path — the test only sees the flag
    // because there is no observer in this isolated hook render.
    expect(qc.getQueryState(['chat', 'session', 's1'])?.isInvalidated).toBe(true);
    // The list re-reads too, so the sidebar/landing list shows the live title.
    await waitFor(() => expect(chat.listSessions).toHaveBeenCalledTimes(2));
    expect(chat.autotitleSession).toHaveBeenCalledWith('s1', 'hi');
  });
});
