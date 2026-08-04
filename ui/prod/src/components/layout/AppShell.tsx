// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useRef } from 'react';
import { Outlet } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Sidebar } from './Sidebar';
import { SessionTimeout } from '@/auth/SessionTimeout';
import { ChatProvider } from '../chat/ChatProvider';
import { ChatBubble } from '../chat/ChatBubble';
import { ChatPanel } from '../chat/ChatPanel';
import { useChatAvailable } from '../chat/useChatAvailable';
import { useDataVersion } from '@/api/pipeline';
import styles from './AppShell.module.css';

/**
 * Drops every client-side cache when a pipeline run finishes.
 *
 * A run rebuilds the asset table from scratch (`TRUNCATE assets CASCADE`), but
 * only the server-side dashboard cache used to be told. Everything else —
 * Explorer, the rule builders, every field picker and value dropdown — kept
 * serving pre-run data until its own staleTime lapsed, 30s to 10min later. The
 * screens an operator uses to ACT on a run's output were the ones still showing
 * the data it replaced.
 *
 * The first answer of a mount only seeds the remembered id — treating it as a
 * change would refetch the whole app on every page load. That holds while the
 * poll is answering: the id that arrives is the one the page was built from. It
 * stops holding once the poll has failed or could not be sent at all (a worker
 * restarting mid-deploy, a transient 5xx, a dropped connection), because a run
 * can finish inside the blind window and the recovery answer then looks exactly
 * like a quiet first load — so a failure before the first answer makes that
 * answer invalidate too. The trade is the safe way round: one extra refetch
 * burst for a tab whose poll failed before it ever answered, against serving
 * rows a run already deleted for the 5-10min the asset queries hold them. The
 * page-load storm the seed rule prevents is the common case; this is not.
 *
 * Exported for its tests. Wrapper isolates the once-a-minute poll so the
 * surrounding AppShell does not re-render on every tick — same reason as
 * ChatWidget below.
 */
export function DataVersionWatcher() {
  const { data, isError, isPaused } = useDataVersion();
  const queryClient = useQueryClient();
  // `undefined` = nothing seen yet; `null` = seen, but no run has finished.
  const seenRunId = useRef<string | null | undefined>(undefined);
  // Only ever set while `data` is still absent: once an answer lands there is
  // no way left to tell a quiet page load from a recovery. A retry still in
  // flight is deliberately not counted, even though `failureCount` would see
  // it: it holds the window open for about a second, no worse than the mount
  // race the seed rule already lives with, whereas the two states below hold it
  // open until the outage ends.
  const sawFailedPoll = useRef(false);

  useEffect(() => {
    // Nothing has loaded yet (first paint, or the poll failed). Seeding from
    // this would make the id's arrival look like a change and refetch the whole
    // app on every page load.
    //
    // A poll the browser never sent counts as one that failed: under the
    // default `networkMode: 'online'` it waits at `fetchStatus: 'paused'` and
    // never reaches an error state, so keying off `isError` alone would leave
    // the window a dropped connection opens unrecorded.
    if (!data) {
      if (isError || isPaused) sawFailedPoll.current = true;
      return;
    }

    const runId = data.run_id;
    if (seenRunId.current === undefined) {
      seenRunId.current = runId;
      // A first answer that follows a failure can be the first sight of a run
      // that finished unseen, so it invalidates as well — unless no run has
      // ever finished, when there is nothing the window could have hidden.
      if (runId === null || !sawFailedPoll.current) return;
      sawFailedPoll.current = false;
    } else {
      if (runId === null || runId === seenRunId.current) return;
      seenRunId.current = runId;
    }

    // No filter, deliberately: only MOUNTED queries refetch, the rest are just
    // marked stale, so the cost is one refetch burst per run per open tab — and
    // there is no list of query keys to keep in sync as hooks are added, which
    // is exactly how a hand-maintained list goes quietly out of date.
    //
    // This also refetches the data-version query itself, which answers with the
    // id that triggered it, so the comparison above does not re-fire. Keep it
    // that way: invalidating on every settle would loop forever.
    queryClient.invalidateQueries();
  }, [data, isError, isPaused, queryClient]);

  return null;
}

function ChatWidget() {
  // Wrapper isolates the useChatAvailable subscription so the surrounding
  // AppShell doesn't re-render when the health query refetches.
  const available = useChatAvailable();
  if (!available) return null;
  return (
    <>
      <ChatBubble />
      <ChatPanel />
    </>
  );
}

export function AppShell() {
  return (
    <ChatProvider>
      <div className={styles.shell}>
        <Sidebar />
        <main className={styles.main}>
          <Outlet />
        </main>
        <SessionTimeout />
        <DataVersionWatcher />
        <ChatWidget />
      </div>
    </ChatProvider>
  );
}
