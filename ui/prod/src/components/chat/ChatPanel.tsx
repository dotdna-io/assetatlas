// SPDX-License-Identifier: AGPL-3.0-or-later
import { useCallback, useRef, useState } from 'react';

import { useChatUI } from './ChatProvider';
import { useChatSession } from './hooks/useChatSession';
import { useConversationHistory } from './hooks/useConversationHistory';
import { ChatHeader } from './ChatHeader';
import { ChatInput } from './ChatInput';
import { MessageList } from './MessageList';
import { ConversationList } from './ConversationList';
import { RenameSessionModal } from './RenameSessionModal';
import { WelcomePanel } from './WelcomePanel';
import { Button } from '../ui/Button';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import type { ChatSession } from '../../api/chat';
import styles from './ChatPanel.module.css';

// Must match DEFAULT_SESSION_TITLE in src/api/blueprints/chat/models.py —
// equality decides whether an auto-title is safe to apply (renamed sessions
// must not be clobbered).
const DEFAULT_SESSION_TITLE = 'Untitled conversation';

export function ChatPanel() {
  const { open, setOpen, currentSessionId, setCurrentSessionId } = useChatUI();
  const history = useConversationHistory();
  // Skip the fetch when the panel is closed — the persisted currentSessionId
  // can outlive the row server-side (account switch, deleted session) and
  // would 404 on every focus.
  const onSessionGone = useCallback(
    () => setCurrentSessionId(null),
    [setCurrentSessionId],
  );
  const session = useChatSession(
    open ? currentSessionId : null,
    { onSessionGone },
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [renaming, setRenaming] = useState<ChatSession | null>(null);
  const [deleting, setDeleting] = useState<ChatSession | null>(null);
  // Session ids we've already fired an auto-title for. Guards against a fast
  // double-submit during the post-send invalidation window where React Query
  // still reports messages.length === 0.
  const autotitledRef = useRef<Set<string>>(new Set());

  if (!open) return null;

  const handleNewConversation = () => {
    history.create.mutate(undefined, {
      onSuccess: (s) => setCurrentSessionId(s.session_id),
    });
  };

  const handleRename = (s: ChatSession) => setRenaming(s);
  const handleDelete = (s: ChatSession) => setDeleting(s);

  const confirmRename = (title: string) => {
    if (!renaming) return;
    const target = renaming;
    history.rename.mutate(
      { id: target.id, title },
      { onSettled: () => setRenaming(null) },
    );
  };

  const confirmDelete = () => {
    if (!deleting) return;
    const target = deleting;
    history.remove.mutate(target.id, {
      onSettled: () => setDeleting(null),
      onSuccess: () => {
        if (currentSessionId === target.id) setCurrentSessionId(null);
      },
    });
  };

  const handleSend = async (text: string) => {
    const sid = currentSessionId;
    // Guard: only fire auto-title when the session still has the default
    // title (i.e. the user hasn't renamed it). Never clobber a user title.
    const needsAutoTitle =
      session.session?.title === DEFAULT_SESSION_TITLE;
    // Fire auto-title regardless of the turn outcome (#735 P4). A turn that
    // errors or is aborted should still get a title — the previous gate on
    // `outcome === 'ok'` left the session stuck at "Untitled conversation".
    // The `autotitledRef` guard prevents duplicate fires during the React
    // Query invalidation window; `onSettled` clears it when the title is
    // still default so a failed attempt can retry on the next message.
    if (needsAutoTitle && sid && !autotitledRef.current.has(sid)) {
      autotitledRef.current.add(sid);
      history.autotitle.mutate(
        { id: sid, userText: text },
        {
          onSettled: (updatedSession, error) => {
            if (error || updatedSession?.title === DEFAULT_SESSION_TITLE) {
              autotitledRef.current.delete(sid);
            }
          },
        },
      );
    }
    await session.send(text);
  };

  const titleText = currentSessionId
    ? (session.session?.title ?? 'New conversation')
    : 'Conversations';

  return (
    <div
      className={`${styles.panel} ${
        sidebarOpen && currentSessionId ? styles.panelWithSidebar : ''
      }`}
      role="dialog"
      aria-label="Chat assistant"
    >
      <ChatHeader
        title={titleText}
        onNewConversation={handleNewConversation}
        onToggleSidebar={
          currentSessionId ? () => setSidebarOpen((v) => !v) : undefined
        }
        sidebarOpen={sidebarOpen}
      />
      <div className={styles.body}>
        {sidebarOpen && currentSessionId && (
          <ConversationList
            sessions={history.sessions}
            currentSessionId={currentSessionId}
            onSelect={(id) => setCurrentSessionId(id)}
            onRename={handleRename}
            onDelete={handleDelete}
          />
        )}
        <div className={styles.main}>
          {!currentSessionId ? (
            history.isLoading ? (
              <div className={styles.loading}>Loading conversations…</div>
            ) : history.sessions.length === 0 ? (
              // Only surface a create failure when there's nothing else to
              // show — a stale create error must never hide existing
              // conversations the user could still pick from.
              history.create.isError ? (
                <div className={styles.loading}>
                  {`Couldn't start a new conversation: ${
                    (history.create.error as any)?.message ?? 'unknown error'
                  }. Check that chat is enabled and you're signed in, then try again.`}
                </div>
              ) : (
                <div className={styles.emptyState}>
                  <p className={styles.emptyText}>No conversations yet.</p>
                  <Button
                    variant="success"
                    size="sm"
                    onClick={handleNewConversation}
                    loading={history.create.isPending}
                  >
                    Start a conversation
                  </Button>
                </div>
              )
            ) : (
              <ConversationList
                variant="main"
                sessions={history.sessions}
                currentSessionId={null}
                onSelect={(id) => setCurrentSessionId(id)}
                onRename={handleRename}
                onDelete={handleDelete}
              />
            )
          ) : (
            <>
              {session.isLoading && session.messages.length === 0 ? (
                // A resumed conversation's messages aren't cached yet — show a
                // loader instead of flashing the WelcomePanel for a session
                // that actually has history.
                <div className={styles.loading}>Loading…</div>
              ) : session.messages.length === 0 && !session.streaming ? (
                <WelcomePanel
                  onPrompt={(text) => { void handleSend(text); }}
                  disabled={session.streaming}
                />
              ) : (
                <MessageList
                  messages={session.messages}
                  streaming={session.streaming}
                  streamingText={session.assistantText}
                  streamingToolCalls={session.toolCalls}
                  error={session.error}
                  onRegenerate={(text) => { void handleSend(text); }}
                />
              )}
              <ChatInput
                onSend={(text) => { void handleSend(text); }}
                onCancel={session.cancel}
                streaming={session.streaming}
                disabled={!currentSessionId}
              />
            </>
          )}
        </div>
      </div>
      <RenameSessionModal
        key={renaming?.id ?? 'none'}
        isOpen={!!renaming}
        initialTitle={renaming?.title ?? ''}
        onSubmit={confirmRename}
        onCancel={() => setRenaming(null)}
        busy={history.rename.isPending}
      />
      <ConfirmDialog
        isOpen={!!deleting}
        title="Delete conversation"
        message={`Delete "${deleting?.title ?? ''}"? This cannot be undone.`}
        confirmLabel="Delete"
        cancelLabel="Cancel"
        variant="danger"
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}
