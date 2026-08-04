// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import * as chat from '../../../api/chat';
import type {
  ChatSession, GetSessionResponse, ListSessionsResponse,
} from '../../../api/chat';

const SESSIONS_KEY = ['chat', 'sessions'] as const;

export function useConversationHistory() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: SESSIONS_KEY });

  const listQ = useQuery({
    queryKey: SESSIONS_KEY,
    queryFn: () => chat.listSessions({ perPage: 50 }),
  });

  const rename = useMutation({
    mutationFn: (v: { id: string; title: string }) =>
      chat.renameSession(v.id, v.title),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: (id: string) => chat.deleteSession(id),
    onSuccess: invalidate,
  });

  const create = useMutation({
    mutationFn: (title?: string) => chat.createSession(title),
    onSuccess: invalidate,
  });

  const autotitle = useMutation({
    mutationFn: (v: { id: string; userText: string }) =>
      chat.autotitleSession(v.id, v.userText),
    onSuccess: (session) => {
      // Show the generated title instantly…
      _patchSessionCaches(qc, session);
      // …then re-read authoritatively. The title was just persisted, but
      // session.send()'s post-stream invalidation may have kicked off a
      // getSession refetch that read the DB *before* the title existed.
      // invalidateQueries defaults to cancelRefetch:true, so it cancels that
      // in-flight stale refetch and replaces it with a fresh read of the
      // persisted title + latest messages. Without this, the stale refetch
      // can settle last and clobber the title — it then only shows up after a
      // manual page reload.
      qc.invalidateQueries({ queryKey: ['chat', 'session', session.id] });
      qc.invalidateQueries({ queryKey: SESSIONS_KEY });
    },
  });

  return {
    sessions: listQ.data?.sessions ?? [],
    total: listQ.data?.total ?? 0,
    isLoading: listQ.isLoading,
    isError: listQ.isError,
    rename,
    remove,
    create,
    autotitle,
  };
}

function _patchSessionCaches(
  qc: ReturnType<typeof useQueryClient>,
  session: ChatSession,
): void {
  qc.setQueryData<ListSessionsResponse>(SESSIONS_KEY, (prev) => {
    if (!prev) return prev;
    return {
      ...prev,
      sessions: prev.sessions.map(
        (s) => (s.id === session.id ? session : s),
      ),
    };
  });
  qc.setQueryData<GetSessionResponse>(
    ['chat', 'session', session.id],
    (prev) => (prev ? { ...prev, session } : prev),
  );
}
