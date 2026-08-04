// SPDX-License-Identifier: AGPL-3.0-or-later
import { useRef, useState, type KeyboardEvent } from 'react';
import styles from './ChatInput.module.css';

interface ChatInputProps {
  onSend: (message: string) => void;
  onCancel?: () => void;
  disabled?: boolean;
  streaming?: boolean;
  placeholder?: string;
}

export function ChatInput({
  onSend,
  onCancel,
  disabled = false,
  streaming = false,
  placeholder = 'Ask about assets, alerts, or reports…',
}: ChatInputProps) {
  const [value, setValue] = useState('');
  const taRef = useRef<HTMLTextAreaElement>(null);
  // Remember the last submitted text so Stop can drop it back into the box for
  // editing ("keep & edit") instead of losing what the user typed.
  const lastSentRef = useRef('');

  const resize = () => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 140)}px`;
  };

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled || streaming) return;
    lastSentRef.current = trimmed;
    onSend(trimmed);
    setValue('');
    if (taRef.current) taRef.current.style.height = 'auto';
  };

  const stop = () => {
    onCancel?.();
    // Restore the just-sent prompt so the user can tweak and resend it — but
    // don't clobber a draft the user typed in the box while the turn streamed.
    const text = lastSentRef.current;
    if (!text || value.trim()) return;
    setValue(text);
    // Height + caret need the value applied first; defer one frame.
    requestAnimationFrame(() => {
      const ta = taRef.current;
      if (!ta) return;
      resize();
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    });
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className={styles.wrap}>
      <div className={styles.row}>
        <textarea
          ref={taRef}
          className={styles.textarea}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          onInput={resize}
          placeholder={placeholder}
          disabled={disabled}
          rows={1}
          aria-label="Chat message"
        />
        {streaming && onCancel ? (
          <button
            type="button"
            className={styles.send}
            onClick={stop}
            aria-label="Stop streaming"
          >
            Stop
          </button>
        ) : (
          <button
            type="button"
            className={styles.send}
            onClick={submit}
            disabled={disabled || !value.trim()}
            aria-label="Send message"
          >
            Send
          </button>
        )}
      </div>
    </div>
  );
}
