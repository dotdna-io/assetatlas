// SPDX-License-Identifier: AGPL-3.0-or-later
import { useCallback, useReducer, useRef } from 'react';

import { apiClient } from '../../../api/client';
import { getAccessToken } from '../../../auth/auth-service';
import type { ChartArtifact } from '../chartArtifactTypes';
import { parseArtifact } from '../chartArtifactTypes';

export type ToolCallStatus = 'running' | 'ok' | 'error';

export interface ToolCallInfo {
  id: string;
  name: string;
  status: ToolCallStatus;
  // Populated from the SSE tool.end payload when the tool produced one;
  // Message.tsx reads this to render a chart inline while the history
  // reload (which persists the same artifact) is still in flight.
  artifact?: ChartArtifact | null;
}

export type StreamState = 'idle' | 'streaming' | 'error';

/** Mirror of backend `StreamEventKind` (src/api/blueprints/chat/service.py). */
export type StreamEventKind =
  | 'message.start'
  | 'delta'
  | 'tool.start'
  | 'tool.end'
  | 'message.end'
  | 'error'
  | 'done';

interface State {
  state: StreamState;
  assistantText: string;
  toolCalls: ToolCallInfo[];
  error: string | null;
  currentMessageId: string | null;
}

type Action =
  | { type: 'start' }
  | { type: 'delta'; text: string }
  | { type: 'tool.start'; id: string; name: string }
  | { type: 'tool.end'; id: string; ok: boolean;
      artifact: ChartArtifact | null }
  | { type: 'assistant.id'; id: string }
  | { type: 'error'; code: string }
  | { type: 'finish' }
  | { type: 'abort' };

const INIT: State = {
  state: 'idle',
  assistantText: '',
  toolCalls: [],
  error: null,
  currentMessageId: null,
};

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case 'start':
      return { ...INIT, state: 'streaming' };
    case 'delta':
      return { ...s, assistantText: s.assistantText + a.text };
    case 'tool.start':
      return {
        ...s,
        toolCalls: [...s.toolCalls, { id: a.id, name: a.name, status: 'running' }],
      };
    case 'tool.end':
      return {
        ...s,
        toolCalls: s.toolCalls.map((c) =>
          c.id === a.id
            ? { ...c, status: a.ok ? 'ok' : 'error', artifact: a.artifact }
            : c,
        ),
      };
    case 'assistant.id':
      return s.currentMessageId === a.id ? s : { ...s, currentMessageId: a.id };
    case 'error':
      return { ...s, error: a.code, state: 'error' };
    case 'finish':
      return s.state === 'error' ? s : { ...s, state: 'idle' };
    case 'abort':
      return { ...s, state: 'idle' };
  }
}

interface UseSSEStreamOptions {
  /** Path relative to the /api base, e.g. "/chat/stream". */
  path: string;
}

interface StartBody {
  session_id: string;
  message: string;
}

/** Outcome of one streaming turn; used by callers to decide whether to run
 *  follow-up work (e.g. auto-title the session) only on a clean turn. */
export type StreamOutcome = 'ok' | 'error' | 'aborted';

/** POST-based SSE hook. Native `EventSource` can't POST a JSON body, so we
 *  parse `event:` / `data:` frames manually off the fetch `ReadableStream`. */
export function useSSEStream(opts: UseSSEStreamOptions) {
  const [state, dispatch] = useReducer(reducer, INIT);
  const abortRef = useRef<AbortController | null>(null);

  const handleFrame = useCallback((raw: string): StreamEventKind | null => {
    let eventName: string = 'message';
    let dataLine = '';
    for (const ln of raw.split('\n')) {
      if (ln.startsWith('event:')) eventName = ln.slice(6).trim();
      else if (ln.startsWith('data:')) dataLine += ln.slice(5).trim();
    }
    let payload: any = {};
    if (dataLine) {
      try { payload = JSON.parse(dataLine); } catch { return null; }
    }

    const kind = eventName as StreamEventKind;
    switch (kind) {
      case 'message.start':
      case 'message.end':
        if (payload.role === 'assistant' && payload.message_id) {
          dispatch({ type: 'assistant.id', id: payload.message_id });
        }
        return kind;
      case 'delta':
        if (payload.text) dispatch({ type: 'delta', text: payload.text });
        return kind;
      case 'tool.start':
        if (payload.id && payload.name) {
          dispatch({ type: 'tool.start', id: payload.id, name: payload.name });
        }
        return kind;
      case 'tool.end':
        if (payload.id) {
          // Malformed artifacts are skipped; the chip still rides.
          dispatch({
            type: 'tool.end',
            id: payload.id,
            ok: !!payload.ok,
            artifact: parseArtifact(payload.artifact),
          });
        }
        return kind;
      case 'error':
        // Prefer the human-readable message the backend now supplies (an
        // actionable, model-agnostic hint) over the bare code; fall back to
        // the code for frames that carry no message (e.g. rate_limited).
        dispatch({
          type: 'error',
          code: payload.message || payload.code || 'error',
        });
        return kind;
      case 'done':
        return kind;
    }
    return null;
  }, []);

  const start = useCallback(
    async (body: StartBody): Promise<StreamOutcome> => {
      dispatch({ type: 'start' });

      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      const token = getAccessToken();
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
      let sawErrorFrame = false;
      try {
        const resp = await fetch(apiClient.resolveUrl(opts.path), {
          method: 'POST',
          headers,
          body: JSON.stringify(body),
          signal: ctrl.signal,
        });
        if (!resp.ok || !resp.body) {
          dispatch({ type: 'error', code: `HTTP ${resp.status}` });
          return 'error';
        }

        reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf('\n\n')) !== -1) {
            const raw = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            if (raw.trim() && handleFrame(raw) === 'error') {
              sawErrorFrame = true;
            }
          }
        }
        dispatch({ type: 'finish' });
        return sawErrorFrame ? 'error' : 'ok';
      } catch (e: any) {
        if (e?.name === 'AbortError') {
          dispatch({ type: 'abort' });
          return 'aborted';
        }
        dispatch({ type: 'error', code: String(e?.message ?? e) });
        return 'error';
      } finally {
        try { reader?.releaseLock(); } catch {}
        if (abortRef.current === ctrl) abortRef.current = null;
      }
    },
    [opts.path, handleFrame],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: 'abort' });
  }, []);

  return {
    state: state.state,
    assistantText: state.assistantText,
    toolCalls: state.toolCalls,
    error: state.error,
    currentMessageId: state.currentMessageId,
    start,
    cancel,
  };
}
