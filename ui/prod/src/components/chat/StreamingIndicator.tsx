// SPDX-License-Identifier: AGPL-3.0-or-later
import type { ToolCallInfo } from './hooks/useSSEStream';
import styles from './StreamingIndicator.module.css';

interface StreamingIndicatorProps {
  hasText: boolean;
  runningTool: ToolCallInfo | null;
}

export function StreamingIndicator({
  hasText,
  runningTool,
}: StreamingIndicatorProps) {
  if (runningTool) {
    return (
      <div className={styles.row} role="status" aria-live="polite">
        Looking up <code>{runningTool.name}</code>…
      </div>
    );
  }
  if (hasText) return null;
  return (
    <div className={styles.row} role="status" aria-live="polite">
      <span className={styles.dots} aria-hidden="true">
        <span className={styles.dot} />
        <span className={styles.dot} />
        <span className={styles.dot} />
      </span>
      <span>Thinking…</span>
    </div>
  );
}
