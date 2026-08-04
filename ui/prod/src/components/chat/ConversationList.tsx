// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import type { ChatSession } from '../../api/chat';
import { DeleteIconButton } from '../ui/IconButton';
import styles from './ConversationList.module.css';

interface TooltipState {
  text: string;
  x: number;
  y: number;
}

type Bucket = 'Today' | 'Yesterday' | 'Earlier';

interface ConversationListProps {
  sessions: ChatSession[];
  currentSessionId: string | null;
  onSelect: (id: string) => void;
  onRename: (session: ChatSession) => void;
  onDelete: (session: ChatSession) => void;
  variant?: 'sidebar' | 'main';
}

function startOfDayMs(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

function bucketFor(date: Date, nowStartMs: number): Bucket {
  const diffDays = (nowStartMs - startOfDayMs(date)) / 86_400_000;
  if (diffDays <= 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  return 'Earlier';
}

export function groupSessions(
  sessions: ChatSession[],
  now: Date = new Date(),
): Record<Bucket, ChatSession[]> {
  const buckets: Record<Bucket, ChatSession[]> = {
    Today: [], Yesterday: [], Earlier: [],
  };
  const nowStartMs = startOfDayMs(now);
  for (const s of sessions) {
    const raw = s.updated_at || s.created_at;
    const t = raw ? new Date(raw) : now;
    const b = Number.isNaN(t.getTime()) ? 'Earlier' : bucketFor(t, nowStartMs);
    buckets[b].push(s);
  }
  return buckets;
}

export function ConversationList({
  sessions,
  currentSessionId,
  onSelect,
  onRename,
  onDelete,
  variant = 'sidebar',
}: ConversationListProps) {
  const grouped = useMemo(() => groupSessions(sessions), [sessions]);
  const order: Bucket[] = ['Today', 'Yesterday', 'Earlier'];

  // Custom tooltip — rendered via portal so it can escape the sidebar's
  // overflow-clipped scroll container into the chat area. 500ms hover
  // delay before show; immediate hide on leave.
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  const timerRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
  }, []);

  const handleEnter = (text: string, target: HTMLElement) => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      const rect = target.getBoundingClientRect();
      setTooltip({
        text,
        x: rect.left + 8,
        y: rect.top - 6,
      });
    }, 500);
  };

  const handleLeave = () => {
    if (timerRef.current != null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setTooltip(null);
  };

  return (
    <aside
      className={variant === 'main' ? styles.mainList : styles.sidebar}
      aria-label="Chat history"
    >
      <div className={styles.header}>
        <span>History</span>
      </div>
      <div className={styles.list}>
        {sessions.length === 0 ? (
          <div className={styles.empty}>No conversations yet.</div>
        ) : (
          order.map((b) =>
            grouped[b].length === 0 ? null : (
              <div key={b}>
                <div className={styles.groupHeader}>{b}</div>
                {grouped[b].map((s) => (
                  <div
                    key={s.id}
                    role="button"
                    tabIndex={0}
                    className={`${styles.row} ${
                      s.id === currentSessionId ? styles.rowActive : ''
                    }`}
                    onClick={() => onSelect(s.id)}
                    onMouseEnter={(e) => handleEnter(s.title || 'Untitled', e.currentTarget)}
                    onMouseLeave={handleLeave}
                    onFocus={(e) => handleEnter(s.title || 'Untitled', e.currentTarget)}
                    onBlur={handleLeave}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        onSelect(s.id);
                      }
                    }}
                  >
                    <span className={styles.title}>
                      {s.title || 'Untitled'}
                    </span>
                    <div className={styles.actions}>
                      <button
                        type="button"
                        className={styles.iconBtn}
                        aria-label={`Rename ${s.title}`}
                        title="Rename"
                        onClick={(e) => {
                          e.stopPropagation();
                          onRename(s);
                        }}
                      >
                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                        </svg>
                      </button>
                      <DeleteIconButton
                        className={styles.iconBtn}
                        aria-label={`Delete ${s.title}`}
                        title="Delete"
                        onClick={(e) => {
                          e.stopPropagation();
                          onDelete(s);
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            ),
          )
        )}
      </div>
      {tooltip && createPortal(
        <div
          className={styles.tooltip}
          style={{ left: tooltip.x, top: tooltip.y }}
          role="tooltip"
        >
          {tooltip.text}
        </div>,
        document.body,
      )}
    </aside>
  );
}
