// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';

import { type AlertChannel } from '@/api/channels';
import { type EmailSettings } from '@/api/settings';
import { useApiQuery } from '@/api/hooks';
import { SettingsSection } from '@/components/settings';
import { Button } from '@/components/ui/Button';
import styles from './EmailChannelForm.module.css';

interface EmailChannelFormProps {
  channel?: AlertChannel;
  onConfigChange: (config: Record<string, unknown>) => void;
}

function initialRecipients(channel: AlertChannel | undefined): string[] {
  const r = channel?.config?.recipients;
  const list = Array.isArray(r) ? r.filter((x): x is string => typeof x === 'string') : [];
  return list.length > 0 ? list : [''];
}

export function EmailChannelForm({ channel, onConfigChange }: EmailChannelFormProps) {
  const [recipients, setRecipients] = useState<string[]>(() => initialRecipients(channel));
  const { data: email } = useApiQuery<EmailSettings>(['email-settings'], '/settings/email');
  const smtpUnconfigured = !!email && (!email.enabled || !email.smtp_host);

  function emit(next: string[]) {
    setRecipients(next);
    onConfigChange({ recipients: next.map((r) => r.trim()).filter(Boolean) });
  }

  function updateAt(index: number, value: string) {
    emit(recipients.map((r, i) => (i === index ? value : r)));
  }

  function removeAt(index: number) {
    const next = recipients.filter((_, i) => i !== index);
    emit(next.length > 0 ? next : ['']);
  }

  function addRow() {
    emit([...recipients, '']);
  }

  return (
    <SettingsSection title="Email configuration">
      <div className={styles.cardBody}>
        <label className={styles.label}>Recipients</label>
        {recipients.map((value, index) => (
          <div key={index} className={styles.recipientRow}>
            <input
              aria-label={`Recipient ${index + 1}`}
              value={value}
              onChange={(e) => updateAt(index, e.target.value)}
              placeholder="alerts@example.com"
            />
            <Button
              variant="secondary"
              size="sm"
              onClick={() => removeAt(index)}
              aria-label={`Remove recipient ${index + 1}`}
            >
              Remove
            </Button>
          </div>
        ))}
        <Button variant="secondary" size="sm" className={styles.addBtn} onClick={addRow}>
          Add recipient
        </Button>

        {smtpUnconfigured && (
          <p className={styles.hint} role="status">
            SMTP is not configured — set it in Settings → Email for this channel to deliver.
          </p>
        )}
      </div>
    </SettingsSection>
  );
}
