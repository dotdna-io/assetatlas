// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';

import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import styles from './RenameSessionModal.module.css';

interface RenameSessionModalProps {
  isOpen: boolean;
  initialTitle: string;
  onSubmit: (newTitle: string) => void;
  onCancel: () => void;
  busy?: boolean;
}

export function RenameSessionModal({
  isOpen,
  initialTitle,
  onSubmit,
  onCancel,
  busy,
}: RenameSessionModalProps) {
  // Caller is expected to pass `key` scoped to the target session so the
  // component remounts with a fresh input when opened on a different session.
  const [value, setValue] = useState(initialTitle);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || trimmed === initialTitle) {
      onCancel();
      return;
    }
    onSubmit(trimmed);
  };

  return (
    <Modal isOpen={isOpen} onClose={onCancel} title="Rename conversation" size="sm">
      <div className={styles.field}>
        <label className={styles.label} htmlFor="chat-rename-input">
          Title
        </label>
        <input
          id="chat-rename-input"
          className={styles.input}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); submit(); }
          }}
          autoFocus
          maxLength={200}
        />
      </div>
      <div className={styles.footer}>
        <Button variant="secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button variant="primary" onClick={submit} loading={busy}
                disabled={!value.trim()}>
          Save
        </Button>
      </div>
    </Modal>
  );
}
