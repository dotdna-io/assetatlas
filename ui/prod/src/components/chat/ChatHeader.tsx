// SPDX-License-Identifier: AGPL-3.0-or-later
import styles from './ChatHeader.module.css';

interface ChatHeaderProps {
  title: string;
  onNewConversation: () => void;
  onToggleSidebar?: () => void;
  sidebarOpen?: boolean;
}

export function ChatHeader({
  title,
  onNewConversation,
  onToggleSidebar,
  sidebarOpen,
}: ChatHeaderProps) {
  return (
    <div className={styles.header}>
      {onToggleSidebar && (
        <button
          type="button"
          className={styles.iconBtn}
          onClick={onToggleSidebar}
          aria-label={sidebarOpen ? 'Hide history' : 'Show history'}
          aria-pressed={sidebarOpen}
          title={sidebarOpen ? 'Hide history' : 'Show history'}
        >
          ☰
        </button>
      )}
      <h2 className={styles.title}>{title}</h2>
      <div className={styles.actions}>
        {/* No close button — the floating chat bubble toggles the panel. */}
        <button
          type="button"
          className={`${styles.iconBtn} ${styles.newBtn}`}
          onClick={onNewConversation}
          aria-label="New conversation"
          title="New conversation"
        >
          <svg
            viewBox="0 0 24 24"
            width="20"
            height="20"
            fill="none"
            stroke="currentColor"
            strokeWidth="3"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
        </button>
      </div>
    </div>
  );
}
