// SPDX-License-Identifier: AGPL-3.0-or-later
import { useChatUI } from './ChatProvider';
import styles from './ChatBubble.module.css';

export function ChatBubble() {
  const { open, setOpen } = useChatUI();
  return (
    <button
      type="button"
      className={styles.bubble}
      aria-label={open ? 'Close chat' : 'Open chat'}
      aria-pressed={open}
      onClick={() => setOpen(!open)}
    >
      <svg className={styles.icon} viewBox="0 0 24 24" aria-hidden="true">
        <defs>
          <linearGradient id="chatStroke" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="#6ee7b7" />
            <stop offset="100%" stopColor="#10b981" />
          </linearGradient>
        </defs>
        <path
          d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
          fill="none"
          stroke="url(#chatStroke)"
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <line x1="7" y1="9" x2="17" y2="9" stroke="url(#chatStroke)" strokeWidth="1.6" strokeLinecap="round" />
        <line x1="7" y1="12" x2="17" y2="12" stroke="url(#chatStroke)" strokeWidth="1.6" strokeLinecap="round" />
        <line x1="7" y1="15" x2="13" y2="15" stroke="url(#chatStroke)" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    </button>
  );
}
