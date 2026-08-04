// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import styles from './InlineStatus.module.css';

export type InlineStatusKind = 'success' | 'error';

export interface InlineStatusValue {
  kind: InlineStatusKind;
  message: string;
  /**
   * Which action produced this status. When set on a success, the flash
   * overlay is skipped — the clicked button renders the confirmation
   * instead (Button's `success` prop, wired with successFor below). The
   * message is still announced to screen readers.
   */
  action?: string;
}

/** The status if it's a success produced by `action`, else null. Feed the
 *  result to Button's `success` prop so only the clicked button flashes. */
export function successFor(
  status: InlineStatusValue | null,
  action: string,
): InlineStatusValue | null {
  return status && status.kind === 'success' && status.action === action ? status : null;
}

export type InlineStatusAlign = 'center' | 'inline';

export interface InlineStatusProps {
  status: InlineStatusValue | null;
  /**
   * 'center' (default) — block, centered, with bottom margin. Use inside
   * cards / boxes / modals.
   * 'inline' — inline-block, sits next to the action button.
   * (Applies to error rendering; success renders as a flash overlay.)
   */
  align?: InlineStatusAlign;
}

/** Centered glass flash for success confirmations: fades in over the page,
 *  holds for under a second, fades out. Always on top (above modals) and
 *  pointer-transparent so it can never block a click. */
function SuccessFlash({ status }: { status: InlineStatusValue }) {
  const [phase, setPhase] = useState<'in' | 'out' | 'done'>('in');

  // `status` (object identity, not text) drives the restart so firing the
  // same message twice replays the flash.
  useEffect(() => {
    setPhase('in');
    const hold = setTimeout(() => setPhase('out'), 900);
    const done = setTimeout(() => setPhase('done'), 1250);
    return () => {
      clearTimeout(hold);
      clearTimeout(done);
    };
  }, [status]);

  if (phase === 'done') return null;
  return createPortal(
    <div
      className={`${styles.flashOverlay} ${phase === 'out' ? styles.flashOut : ''}`}
      role="status"
    >
      <div className={styles.flashPane}>
        <svg
          viewBox="0 0 24 24"
          width="18"
          height="18"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="20 6 9 17 4 12" />
        </svg>
        {status.message}
      </div>
    </div>,
    document.body,
  );
}

export function InlineStatus({ status, align = 'center' }: InlineStatusProps) {
  if (!status) return null;
  if (status.kind === 'success') {
    if (status.action) {
      // A button owns this confirmation (Button `success` prop) — keep the
      // message available to screen readers only.
      return (
        <span className={styles.srOnly} role="status">
          {status.message}
        </span>
      );
    }
    return <SuccessFlash status={status} />;
  }
  const layoutCls = align === 'inline' ? styles.inline : styles.center;
  return (
    <span className={`${styles.status} ${layoutCls} ${styles.error}`} role="alert">
      {status.message}
    </span>
  );
}

/**
 * Hook for an inline status banner. Auto-clears after `timeout` ms (default 5s).
 * Pass timeout=0 to disable auto-clear.
 */
export function useInlineStatus(timeout = 5000) {
  const [status, setStatus] = useState<InlineStatusValue | null>(null);

  useEffect(() => {
    if (!status || timeout <= 0) return;
    const id = setTimeout(() => setStatus(null), timeout);
    return () => clearTimeout(id);
  }, [status, timeout]);

  return [status, setStatus] as const;
}
