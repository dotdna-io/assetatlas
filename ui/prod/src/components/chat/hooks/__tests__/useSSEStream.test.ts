// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

import { useSSEStream } from '../useSSEStream';
import { makeDonutArtifact } from '../../__tests__/fixtures';

function sseStream(frames: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(ctrl) {
      const enc = new TextEncoder();
      for (const frame of frames) ctrl.enqueue(enc.encode(frame));
      ctrl.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('access_token', 'jwt-test');
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useSSEStream', () => {
  it('accumulates delta text and ends idle', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseStream([
        'event: message.start\ndata: {"message_id":"u1","role":"user"}\n\n',
        'event: delta\ndata: {"text":"he"}\n\n',
        'event: delta\ndata: {"text":"llo"}\n\n',
        'event: message.end\ndata: {"message_id":"a1","role":"assistant"}\n\n',
        'event: done\ndata: {}\n\n',
      ]),
    );
    (globalThis as any).fetch = fetchMock;

    const { result } = renderHook(() => useSSEStream({ path: '/chat/stream' }));
    await act(async () => {
      await result.current.start({ session_id: 's', message: 'hi' });
    });

    expect(result.current.assistantText).toBe('hello');
    expect(result.current.state).toBe('idle');
    expect(result.current.currentMessageId).toBe('a1');
    expect(result.current.error).toBeNull();

    const call = fetchMock.mock.calls[0];
    expect(call[1].method).toBe('POST');
    expect(call[1].headers.Authorization).toBe('Bearer jwt-test');
    expect(call[1].headers.Accept).toBe('text/event-stream');
    expect(JSON.parse(call[1].body)).toEqual({ session_id: 's', message: 'hi' });
  });

  it('records tool start/end events', async () => {
    (globalThis as any).fetch = vi.fn().mockResolvedValue(
      sseStream([
        'event: tool.start\ndata: {"id":"t1","name":"list_assets"}\n\n',
        'event: tool.end\ndata: {"id":"t1","name":"list_assets","ok":true,"status":"ok"}\n\n',
        'event: done\ndata: {}\n\n',
      ]),
    );

    const { result } = renderHook(() => useSSEStream({ path: '/chat/stream' }));
    await act(async () => {
      await result.current.start({ session_id: 's', message: 'hi' });
    });

    expect(result.current.toolCalls).toEqual([
      { id: 't1', name: 'list_assets', status: 'ok', artifact: null },
    ]);
  });

  it('attaches a donut artifact from tool.end onto the ToolCallInfo', async () => {
    const artifact = makeDonutArtifact({
      series: [{ label: 'server', value: 5 }],
    });
    (globalThis as any).fetch = vi.fn().mockResolvedValue(
      sseStream([
        'event: tool.start\ndata: {"id":"t1","name":"aggregate_assets"}\n\n',
        `event: tool.end\ndata: ${JSON.stringify({
          id: 't1', name: 'aggregate_assets', ok: true, status: 'ok',
          artifact,
        })}\n\n`,
        'event: done\ndata: {}\n\n',
      ]),
    );

    const { result } = renderHook(() => useSSEStream({ path: '/chat/stream' }));
    await act(async () => {
      await result.current.start({ session_id: 's', message: 'chart' });
    });

    expect(result.current.toolCalls[0].artifact).toEqual(artifact);
  });

  it('drops a malformed artifact without breaking the tool chip', async () => {
    (globalThis as any).fetch = vi.fn().mockResolvedValue(
      sseStream([
        'event: tool.start\ndata: {"id":"t1","name":"aggregate_assets"}\n\n',
        // Missing required "series" key — parseArtifact must reject.
        'event: tool.end\ndata: {"id":"t1","ok":true,"status":"ok",'
          + '"artifact":{"type":"donut","title":"x"}}\n\n',
        'event: done\ndata: {}\n\n',
      ]),
    );

    const { result } = renderHook(() => useSSEStream({ path: '/chat/stream' }));
    await act(async () => {
      await result.current.start({ session_id: 's', message: 'chart' });
    });

    expect(result.current.toolCalls[0].status).toBe('ok');
    expect(result.current.toolCalls[0].artifact).toBeNull();
  });

  it('marks tool.end with ok=false as error', async () => {
    (globalThis as any).fetch = vi.fn().mockResolvedValue(
      sseStream([
        'event: tool.start\ndata: {"id":"t1","name":"list_assets"}\n\n',
        'event: tool.end\ndata: {"id":"t1","name":"list_assets","ok":false,"status":"error"}\n\n',
        'event: done\ndata: {}\n\n',
      ]),
    );
    const { result } = renderHook(() => useSSEStream({ path: '/chat/stream' }));
    await act(async () => {
      await result.current.start({ session_id: 's', message: 'hi' });
    });
    expect(result.current.toolCalls[0].status).toBe('error');
  });

  it('captures error frames by code', async () => {
    (globalThis as any).fetch = vi.fn().mockResolvedValue(
      sseStream([
        'event: error\ndata: {"code":"rate_limited","count":50,"remaining":0}\n\n',
        'event: done\ndata: {}\n\n',
      ]),
    );
    const { result } = renderHook(() => useSSEStream({ path: '/chat/stream' }));
    await act(async () => {
      await result.current.start({ session_id: 's', message: 'hi' });
    });
    expect(result.current.error).toBe('rate_limited');
  });

  it('prefers the human-readable message over the code on error frames', async () => {
    // The backend sends a stable code for telemetry plus an actionable,
    // model-agnostic message; the UI must show the message, not the bare
    // "stream_failed" code (issue #695, Problem 2).
    const human =
      "The selected chat model doesn't support tool use. An administrator " +
      'should choose a tool-capable model in Settings.';
    (globalThis as any).fetch = vi.fn().mockResolvedValue(
      sseStream([
        `event: error\ndata: ${JSON.stringify({ code: 'stream_failed', message: human })}\n\n`,
        'event: done\ndata: {}\n\n',
      ]),
    );
    const { result } = renderHook(() => useSSEStream({ path: '/chat/stream' }));
    await act(async () => {
      await result.current.start({ session_id: 's', message: 'hi' });
    });
    expect(result.current.state).toBe('error');
    expect(result.current.error).toBe(human);
  });

  it('sets error state on non-ok HTTP response', async () => {
    (globalThis as any).fetch = vi.fn().mockResolvedValue(
      new Response('boom', { status: 500 }),
    );
    const { result } = renderHook(() => useSSEStream({ path: '/chat/stream' }));
    await act(async () => {
      await result.current.start({ session_id: 's', message: 'hi' });
    });
    expect(result.current.state).toBe('error');
    expect(result.current.error).toBe('HTTP 500');
  });

  it('handles SSE frames split across chunks', async () => {
    (globalThis as any).fetch = vi.fn().mockResolvedValue(
      sseStream([
        'event: delta\ndata: {"tex',
        't":"hi"}\n\n',
        'event: done\ndata: {}\n\n',
      ]),
    );
    const { result } = renderHook(() => useSSEStream({ path: '/chat/stream' }));
    await act(async () => {
      await result.current.start({ session_id: 's', message: 'hi' });
    });
    expect(result.current.assistantText).toBe('hi');
  });

  it('cancel aborts the in-flight request', async () => {
    let rejectFn: (e: any) => void = () => {};
    const fetchMock = vi.fn().mockImplementation((_url, opts: any) => {
      return new Promise((_resolve, reject) => {
        rejectFn = reject;
        opts.signal.addEventListener('abort', () => {
          const e = new Error('aborted');
          e.name = 'AbortError';
          reject(e);
        });
      });
    });
    (globalThis as any).fetch = fetchMock;

    const { result } = renderHook(() => useSSEStream({ path: '/chat/stream' }));
    act(() => {
      result.current.start({ session_id: 's', message: 'hi' });
    });
    await waitFor(() => expect(result.current.state).toBe('streaming'));
    act(() => { result.current.cancel(); });
    await waitFor(() => expect(result.current.state).toBe('idle'));
    expect(result.current.error).toBeNull();
    // silence the outstanding promise
    rejectFn({ name: 'AbortError' });
  });
});
