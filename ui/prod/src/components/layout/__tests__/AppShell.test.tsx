// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach, vi } from 'vitest';
import React from 'react';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AppShell, DataVersionWatcher } from '../AppShell';
import { useDataVersion } from '@/api/pipeline';

// The whole point of the watcher is to react to the poll, so the poll itself is
// the one thing mocked — every assertion below is about what the REAL component
// does to a REAL QueryClient in response.
vi.mock('@/api/pipeline', () => ({ useDataVersion: vi.fn() }));

// AppShell's other children are irrelevant here and each drag in a router /
// auth context; stub them so the mount test stays about the watcher.
vi.mock('../Sidebar', () => ({ Sidebar: () => null }));
vi.mock('@/auth/SessionTimeout', () => ({ SessionTimeout: () => null }));
vi.mock('../../chat/ChatProvider', () => ({
  ChatProvider: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock('../../chat/ChatBubble', () => ({ ChatBubble: () => null }));
vi.mock('../../chat/ChatPanel', () => ({ ChatPanel: () => null }));
vi.mock('../../chat/useChatAvailable', () => ({ useChatAvailable: () => false }));
vi.mock('react-router-dom', () => ({ Outlet: () => null }));

const mockUseDataVersion = useDataVersion as unknown as ReturnType<typeof vi.fn>;

/** What the endpoint returns for a finished run, or for a fresh install. */
function version(runId: string | null) {
  return runId === null
    ? { run_id: null, completed_at: null, status: null }
    : { run_id: runId, completed_at: '2026-07-29T10:30:00+00:00', status: 'completed' };
}

type PollState = { isError?: boolean; isPaused?: boolean };

/**
 * One poll as TanStack reports it: no `data` and no error is a request still in
 * flight, no `data` with `isError` is one that answered 5xx, no `data` with
 * `isPaused` is one the browser never sent (offline, `networkMode: 'online'` —
 * `isError` stays false there), and a failure after a success keeps the last
 * good `data` alongside `isError`. Tests name only the half they are exercising.
 */
function answer(data: unknown, state: PollState = {}) {
  return {
    data,
    isError: state.isError ?? false,
    isPaused: state.isPaused ?? false,
  };
}

/**
 * Mount the watcher with a first poll result, and hand back a `poll` that
 * feeds it the next one — i.e. the real sequence: page load, then a minute
 * later, then a minute after that.
 */
function mountWatcher(first: unknown, state: PollState = {}) {
  mockUseDataVersion.mockReturnValue(answer(first, state));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
  const tree = () => (
    <QueryClientProvider client={qc}>
      <DataVersionWatcher />
    </QueryClientProvider>
  );
  const { rerender } = render(tree());
  const poll = (data: unknown, pollState: PollState = {}) => {
    mockUseDataVersion.mockReturnValue(answer(data, pollState));
    rerender(tree());
  };
  return { invalidateSpy, poll };
}

describe('DataVersionWatcher: drops every client cache when a run finishes', () => {
  beforeEach(() => {
    mockUseDataVersion.mockReset();
  });

  it('does not invalidate on the first successful load', () => {
    // Every page load starts with an unknown run id. Treating that first
    // answer as a change would make each navigation refetch the entire app.
    const { invalidateSpy } = mountWatcher(version('run-1'));
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it('does not invalidate when the query has not resolved yet', () => {
    // The real page-load order: `data` is undefined on first paint and the id
    // arrives a moment later. Seeding from `undefined` would make that arrival
    // look like a change — a refetch storm on every single page load.
    const { invalidateSpy, poll } = mountWatcher(undefined);
    poll(version('run-1'));
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it('invalidates when the first answer arrives after a failing poll', () => {
    // Nothing has answered since mount — an API worker restarting through a
    // deploy, a transient 5xx — so a run that finished inside that window would
    // otherwise be seeded as though it had been on screen all along, and the
    // tab would keep serving pre-run rows for the asset queries' 5-10min.
    const { invalidateSpy, poll } = mountWatcher(undefined, { isError: true });
    poll(version('run-1'));

    expect(invalidateSpy).toHaveBeenCalledTimes(1);
    // Unfiltered, exactly as the change path: the run may have replaced
    // anything, and this is the branch that cannot tell what.
    expect(invalidateSpy).toHaveBeenCalledWith();
  });

  it('invalidates when the first answer arrives after a paused poll', () => {
    // A poll the browser never sent — offline under the default
    // `networkMode: 'online'` — reports `isPaused`, never `isError`, so it has
    // to count as a blind window too or the connection dropping between page
    // load and the first answer replays the bug above.
    const { invalidateSpy, poll } = mountWatcher(undefined, { isPaused: true });
    poll(version('run-1'));

    expect(invalidateSpy).toHaveBeenCalledTimes(1);
    expect(invalidateSpy).toHaveBeenCalledWith();
  });

  it('does not invalidate when the answer after a failure has no run', () => {
    // No run has ever finished, so the blind window cannot have hidden one.
    const { invalidateSpy, poll } = mountWatcher(undefined, { isError: true });
    poll(version(null));
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it('invalidates once for a recovery, not on every poll after it', () => {
    // The recovery is a one-off catch-up: the id is known from here on, so the
    // steady state must be the same as any other mount's.
    const { invalidateSpy, poll } = mountWatcher(undefined, { isError: true });
    poll(version('run-1'));
    poll(version('run-1'));
    poll(version('run-1'));

    expect(invalidateSpy).toHaveBeenCalledTimes(1);
  });

  it('invalidates exactly once when the run id changes', () => {
    const { invalidateSpy, poll } = mountWatcher(version('run-1'));
    poll(version('run-2'));

    expect(invalidateSpy).toHaveBeenCalledTimes(1);
    // No filter: everything cached is stale, and a hand-maintained key list
    // would silently miss each hook added after it was written.
    expect(invalidateSpy).toHaveBeenCalledWith();
  });

  it('does nothing when a later poll repeats the same run id', () => {
    // Also the anti-loop guarantee: the unfiltered invalidation refetches the
    // data-version query itself, which answers with the id that caused it.
    const { invalidateSpy, poll } = mountWatcher(version('run-1'));
    poll(version('run-2'));
    poll(version('run-2'));
    poll(version('run-2'));

    expect(invalidateSpy).toHaveBeenCalledTimes(1);
  });

  it('treats a failure after a good answer as nothing to act on', () => {
    // TanStack keeps serving the last good `data` through an error, so this is
    // not a blind window: the id on screen is still known, and the changed id
    // that arrives on recovery takes the ordinary path.
    const { invalidateSpy, poll } = mountWatcher(version('run-1'));
    poll(version('run-1'), { isError: true });
    expect(invalidateSpy).not.toHaveBeenCalled();

    poll(version('run-2'));
    expect(invalidateSpy).toHaveBeenCalledTimes(1);
  });

  it('does nothing while no run has ever finished', () => {
    const { invalidateSpy, poll } = mountWatcher(version(null));
    poll(version(null));
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it('invalidates on the first run to finish after a fresh install', () => {
    const { invalidateSpy, poll } = mountWatcher(version(null));
    poll(version('run-1'));
    expect(invalidateSpy).toHaveBeenCalledTimes(1);
  });

  it('renders nothing', () => {
    mockUseDataVersion.mockReturnValue(answer(version('run-1')));
    const qc = new QueryClient();
    const { container } = render(
      <QueryClientProvider client={qc}>
        <DataVersionWatcher />
      </QueryClientProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe('AppShell mounts the watcher', () => {
  beforeEach(() => {
    mockUseDataVersion.mockReset();
  });

  it('subscribes on every page, since AppShell wraps them all', () => {
    mockUseDataVersion.mockReturnValue(answer(undefined));
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AppShell />
      </QueryClientProvider>,
    );
    expect(mockUseDataVersion).toHaveBeenCalled();
  });
});
