// SPDX-License-Identifier: AGPL-3.0-or-later
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import * as chat from '../../../api/chat';
import { ApiError } from '../../../api/client';
import { useSSEStream } from './useSSEStream';
import type { StreamOutcome } from './useSSEStream';

interface Options {
  /** Called once when the lookup confirms the session is gone (404). */
  onSessionGone?: () => void;
}

// Stable id for the optimistic user bubble shown while a turn streams. Never
// collides with a server message id (those are UUIDs).
const OPTIMISTIC_USER_ID = '__optimistic_user__';

export function useChatSession(
  sessionId: string | null,
  { onSessionGone }: Options = {},
) {
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ['chat', 'session', sessionId],
    queryFn: () => chat.getSession(sessionId!),
    enabled: !!sessionId,
  });

  useEffect(() => {
    if (!sessionId) return;
    const err = q.error;
    if (err instanceof ApiError && err.status === 404) {
      onSessionGone?.();
    }
  }, [sessionId, q.error, onSessionGone]);

  const sse = useSSEStream({ path: '/chat/stream' });
  const { start: sseStart, cancel: sseCancel } = sse;

  // The just-sent user turn, rendered immediately so the prompt never
  // disappears during "Thinking…". Persisted history only arrives on the
  // post-stream refetch (below); until then this optimistic copy stands in.
  // `baseCount` is the persisted message count at send time — once the refetch
  // grows the list past it, the real message has landed and the copy is hidden.
  const [pending, setPending] = useState<
    { text: string; baseCount: number } | null
  >(null);

  const send = useCallback(
    async (message: string): Promise<StreamOutcome> => {
      if (!sessionId) return 'aborted';
      const baseCount =
        qc.getQueryData<chat.GetSessionResponse>([
          'chat', 'session', sessionId,
        ])?.messages.length ?? 0;
      setPending({ text: message, baseCount });
      try {
        return await sseStart({ session_id: sessionId, message });
      } finally {
        // Pull the persisted turn (user message + assistant reply, or just the
        // user message on a cancelled turn) back in, THEN drop the optimistic
        // copy. Awaiting the refetch first means there is no frame where the
        // prompt is missing or shown twice.
        try {
          await qc.invalidateQueries({
            queryKey: ['chat', 'session', sessionId],
          });
        } catch {
          // A refetch failure surfaces through the query itself; handled below.
        }
        qc.invalidateQueries({ queryKey: ['chat', 'sessions'] });
        // Only retire the optimistic copy once the persisted turn has actually
        // landed (the refetch grew the list past the pre-send count). If the
        // refetch failed or returned stale data, keep the optimistic bubble so
        // the user's message doesn't vanish — a later refetch clears it.
        const landed =
          (qc.getQueryData<chat.GetSessionResponse>([
            'chat', 'session', sessionId,
          ])?.messages.length ?? 0) > baseCount;
        if (landed) setPending(null);
      }
    },
    [sessionId, sseStart, qc],
  );

  const messages = useMemo<chat.ChatMessage[]>(() => {
    const base = q.data?.messages ?? [];
    // Hide the optimistic bubble the instant the refetch lands (persisted list
    // grew past the pre-send count) so the real message replaces it without a
    // duplicate flash.
    if (!pending || base.length > pending.baseCount) return base;
    return [
      ...base,
      {
        id: OPTIMISTIC_USER_ID,
        seq: Number.MAX_SAFE_INTEGER,
        role: 'user',
        content: pending.text,
        tool_calls: null,
        input_tokens: null,
        output_tokens: null,
        created_at: '',
      },
    ];
  }, [q.data, pending]);

  return {
    session: q.data?.session ?? null,
    messages,
    isLoading: q.isLoading,
    isError: q.isError,
    streaming: sse.state === 'streaming',
    assistantText: sse.assistantText,
    toolCalls: sse.toolCalls,
    error: sse.error,
    currentMessageId: sse.currentMessageId,
    send,
    cancel: sseCancel,
  };
}
