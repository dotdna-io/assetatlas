// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useRef } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import styles from './users.module.css';

interface InviteCreatedDialogProps {
  isOpen: boolean;
  onClose: () => void;
  username: string;
  email: string;
  inviteUrl: string;
  emailSent: boolean;
  expiresAt: string | null;
}

export function InviteCreatedDialog({
  isOpen,
  onClose,
  username,
  email,
  inviteUrl,
  emailSent,
  expiresAt,
}: InviteCreatedDialogProps) {
  const [copied, setCopied] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!isOpen) setCopied(false);
  }, [isOpen]);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(inviteUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback: select the input so the user can copy manually.
      inputRef.current?.select();
    }
  }

  const expiry = expiresAt
    ? new Date(expiresAt).toLocaleString()
    : 'in 7 days';

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={`Invite created for ${username}`}>
      <div className={styles.inviteCreatedBody}>
        <p>
          {emailSent
            ? `An invitation email has been sent to ${email}.`
            : `Email could not be sent to ${email}. Share the link below with the user directly.`}
        </p>
        <p>The link expires {expiresAt ? `on ${expiry}` : expiry}.</p>
        <label htmlFor="invite-url-input" className={styles.inviteUrlLabel}>
          Onboarding link:
        </label>
        <div className={styles.inviteUrlRow}>
          <input
            id="invite-url-input"
            ref={inputRef}
            type="text"
            value={inviteUrl}
            readOnly
            className={styles.inviteUrlInput}
            onFocus={(e) => e.currentTarget.select()}
          />
          <Button onClick={handleCopy} variant="primary">
            {copied ? 'Copied!' : 'Copy link'}
          </Button>
        </div>
        <div className={styles.inviteCreatedActions}>
          <Button onClick={onClose} variant="secondary">Done</Button>
        </div>
      </div>
    </Modal>
  );
}
