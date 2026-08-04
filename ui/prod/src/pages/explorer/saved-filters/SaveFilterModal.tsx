// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import type { SavedFilterVisibility } from '@/api/saved-filters';
import styles from './saved-filters.module.css';

export interface SaveFilterModalProps {
  isOpen: boolean;
  mode: 'create' | 'edit';
  initialName?: string;
  initialDescription?: string;
  initialVisibility?: SavedFilterVisibility;
  canPublish?: boolean;
  busy?: boolean;
  onSubmit: (value: { name: string; description: string; visibility: SavedFilterVisibility }) => void;
  onCancel: () => void;
}

export function SaveFilterModal({
  isOpen,
  mode,
  initialName = '',
  initialDescription = '',
  initialVisibility = 'private',
  canPublish,
  busy,
  onSubmit,
  onCancel,
}: SaveFilterModalProps) {
  // Caller passes `key` scoped to the target so the inputs reset per open.
  const [name, setName] = useState(initialName);
  const [description, setDescription] = useState(initialDescription);
  // The dialog only chooses private vs library (org, admins only). Link sharing
  // is a separate one-click action on the row (the Share button), so there is no
  // link radio here; an existing 'link'/'org' filter keeps its value (the radios
  // may show neither checked) unless the user explicitly changes it.
  const [visibility, setVisibility] = useState<SavedFilterVisibility>(initialVisibility);

  const submit = () => {
    if (busy) return;  // the Save button is disabled while busy; guard the Enter key too
    const trimmed = name.trim();
    if (!trimmed) return;
    onSubmit({ name: trimmed, description: description.trim(), visibility });
  };

  const title = mode === 'create' ? 'Save filter' : 'Edit filter';

  // Only admins pick access (private vs library); everyone else is private by
  // default and shares via the row Share button. Also shown when editing an
  // existing library filter, so its value is visible and preserved.
  const showAccess = canPublish || visibility === 'org';

  return (
    <Modal isOpen={isOpen} onClose={onCancel} title={title} size="sm">
      <div className={styles.field}>
        <label className={styles.label} htmlFor="saved-filter-name">Name</label>
        <input
          id="saved-filter-name"
          className={styles.input}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } }}
          autoFocus
          maxLength={200}
        />
      </div>
      <div className={styles.field}>
        <label className={styles.label} htmlFor="saved-filter-desc">Description (optional)</label>
        <input
          id="saved-filter-desc"
          className={styles.input}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          maxLength={2000}
        />
      </div>
      {showAccess && (
        <div className={styles.field}>
          <span className={styles.label}>General access</span>
          <label className={styles.radioRow}>
            <input
              type="radio"
              name="sf-visibility"
              checked={visibility === 'private'}
              onChange={() => setVisibility('private')}
            />
            <span><strong>Private</strong> — only you</span>
          </label>
          <label className={styles.radioRow}>
            <input
              type="radio"
              name="sf-visibility"
              checked={visibility === 'org'}
              onChange={() => setVisibility('org')}
              disabled={!canPublish}
            />
            <span><strong>Published to the library</strong> — visible to everyone (admins only)</span>
          </label>
        </div>
      )}
      <div className={styles.modalFooter}>
        <Button variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button>
        <Button variant="primary" onClick={submit} loading={busy} disabled={!name.trim()}>
          Save
        </Button>
      </div>
    </Modal>
  );
}
