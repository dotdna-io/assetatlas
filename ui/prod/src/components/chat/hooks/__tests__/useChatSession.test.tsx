// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import { useChatSession } from '../useChatSession';
import * as chat from '../../../../api/chat';
import { makeDonutArtifact } from '../../__tests__/fixtures';

vi.mock('../../../../api/chat', () => ({
  getSession: vi.fn(),
}));

// Stable mocks so a test can drive a single in-flight stream (control when it
// resolves) instead of getting a fresh vi.fn() on every render.
const sseMocks = vi.hoisted(() => ({
  start: vi.fn(),
  cancel: vi.fn(),
}));

vi.mock('../useSSEStream', () => ({
  useSSEStream: () => ({
    state: 'idle',
    assistantText: '',
    toolCalls: [],
    error: null,
    currentMessageId: null,
    start: sseMocks.start,
    cancel: sseMocks.cancel,
  }),
}));

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  sseMocks.start.mockReset();
  sseMocks.start.mockResolvedValue('ok');
  sseMocks.cancel.mockReset();
});

describe('useChatSession', () => {
  it('does not query when sessionId is null', () => {
    const { result } = renderHook(() => useChatSession(null), {
      wrapper: wrapper(),
    });
    expect(result.current.session).toBeNull();
    expect(chat.getSession).not.toHaveBeenCalled();
  });

  it('loads session and messages when sessionId set', async () => {
    (chat.getSession as any).mockResolvedValue({
      session: { id: 's1', title: 'hi', model: 'x', provider: 'y',
                 created_at: 'a', updated_at: 'b' },
      messages: [{ id: 'm1', seq: 1, role: 'user', content: 'hi',
                   tool_calls: null, input_tokens: null,
                   output_tokens: null, created_at: 'x' }],
    });
    const { result } = renderHook(() => useChatSession('s1'), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.session?.id).toBe('s1');
    expect(result.current.messages).toHaveLength(1);
  });

  it('optimistically shows the sent prompt while streaming, then de-dups', async () => {
    // Reproduces the "prompt disappears during Thinking…" bug: the user's
    // message must be visible immediately, not only after the post-stream
    // refetch — and must not double up once the persisted turn lands.
    const sess = { id: 's1', title: 't', model: 'x', provider: 'y',
                   created_at: 'a', updated_at: 'b' };
    (chat.getSession as any).mockResolvedValue({ session: sess, messages: [] });

    // Hold the stream open so the in-flight state is observable.
    let resolveStream!: (v: string) => void;
    sseMocks.start.mockImplementation(
      () => new Promise<string>((res) => { resolveStream = res; }),
    );

    const { result } = renderHook(() => useChatSession('s1'), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.messages).toHaveLength(0);

    // Sending shows the prompt at once, before any server round-trip.
    act(() => { void result.current.send('how many laptops?'); });
    await waitFor(() =>
      expect(result.current.messages.map((m) => m.content)).toContain(
        'how many laptops?',
      ),
    );
    expect(result.current.messages.at(-1)?.role).toBe('user');

    // Turn finishes: the refetch returns the persisted turn, which replaces
    // the optimistic copy — exactly one user bubble, no duplicate.
    (chat.getSession as any).mockResolvedValue({
      session: sess,
      messages: [
        { id: 'u1', seq: 1, role: 'user', content: 'how many laptops?',
          tool_calls: null, input_tokens: null, output_tokens: null,
          created_at: 'x' },
        { id: 'a1', seq: 2, role: 'assistant', content: '5,728.',
          tool_calls: null, input_tokens: null, output_tokens: null,
          created_at: 'x' },
      ],
    });
    await act(async () => { resolveStream('ok'); });
    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect(
      result.current.messages.filter((m) => m.content === 'how many laptops?'),
    ).toHaveLength(1);
  });

  it('keeps the optimistic prompt visible if the post-stream refetch fails', async () => {
    // Regression guard: the optimistic copy must only be retired once the
    // persisted turn actually lands. If the post-stream refetch errors, the
    // user's message must NOT vanish (that's the bug we were fixing, on the
    // failure path).
    const sess = { id: 's1', title: 't', model: 'x', provider: 'y',
                   created_at: 'a', updated_at: 'b' };
    (chat.getSession as any).mockResolvedValueOnce({
      session: sess, messages: [],
    });
    let resolveStream!: (v: string) => void;
    sseMocks.start.mockImplementation(
      () => new Promise<string>((res) => { resolveStream = res; }),
    );

    const { result } = renderHook(() => useChatSession('s1'), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => { void result.current.send('how many laptops?'); });
    await waitFor(() =>
      expect(result.current.messages.map((m) => m.content)).toContain(
        'how many laptops?',
      ),
    );

    // Every refetch from here on fails (network blip on GET /sessions/{id}).
    (chat.getSession as any).mockRejectedValue(new Error('network'));
    await act(async () => { resolveStream('ok'); });

    // The prompt is still on screen — not silently dropped.
    expect(result.current.messages.map((m) => m.content)).toContain(
      'how many laptops?',
    );
  });

  it('send is a no-op when sessionId is null', async () => {
    const { result } = renderHook(() => useChatSession(null), {
      wrapper: wrapper(),
    });
    await act(async () => {
      await result.current.send('hello');
    });
    // no throw; no getSession fetch triggered
    expect(chat.getSession).not.toHaveBeenCalled();
  });

  it('passes tool_calls with artifacts through from the API response', async () => {
    // The backend's GET /chat/sessions/{id} folds the artifact onto each
    // tool_use entry (see src/api/blueprints/chat/routes.py). Nothing in
    // useChatSession should strip it — the hook is a thin wrapper over
    // the API response; MessageList is what later fans it out to the
    // final answer. This test locks the pass-through.
    const artifact = makeDonutArtifact({
      series: [{ label: 'server', value: 5 }],
    });
    (chat.getSession as any).mockResolvedValue({
      session: { id: 's1', title: 't', model: 'x', provider: 'y',
                 created_at: 'a', updated_at: 'b' },
      messages: [
        { id: 'u1', seq: 1, role: 'user', content: 'q',
          tool_calls: null, input_tokens: null, output_tokens: null,
          created_at: 'x' },
        { id: 'a1', seq: 2, role: 'assistant', content: 'checking',
          tool_calls: [
            { id: 't1', name: 'aggregate_assets', input: {}, artifact },
          ],
          input_tokens: null, output_tokens: null, created_at: 'x' },
        { id: 'a2', seq: 3, role: 'assistant', content: 'done',
          tool_calls: null, input_tokens: null, output_tokens: null,
          created_at: 'x' },
      ],
    });
    const { result } = renderHook(() => useChatSession('s1'), {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    const thinking = result.current.messages[1];
    const toolCalls = thinking.tool_calls as Array<Record<string, unknown>>;
    expect(toolCalls[0].artifact).toEqual(artifact);
  });
});
