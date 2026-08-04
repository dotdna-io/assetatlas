// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from 'react';

import { settingsApi, type EmailSettings, type EmailSecurity } from '@/api/settings';
import { Button } from '@/components/ui/Button';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { InlineStatus, useInlineStatus, successFor } from '@/components/ui/InlineStatus';
import { Modal } from '@/components/ui/Modal';
import { SettingsSection } from '@/components/settings';
import styles from './EmailSettingsTab.module.css';

// Password state is a discriminated union so a stray focus on the field
// cannot silently wipe the stored password: only 'new' or 'clear' cause
// a PUT, and both require an explicit user action.
type PasswordDraft =
  | { mode: 'unchanged' }  // initial state while a password is stored server-side
  | { mode: 'new'; value: string }  // user typed a new password
  | { mode: 'clear' }      // user explicitly chose to remove the password
  | { mode: 'empty' };     // initial state when no password is stored

interface FormState {
  enabled: boolean;
  smtp_host: string;
  smtp_port: string;       // kept as string for <input type="number">
  smtp_security: EmailSecurity;
  smtp_username: string;
  password: PasswordDraft;
  from_address: string;
  from_display_name: string;
  reply_to_address: string;
}

function formFromSettings(s: EmailSettings): FormState {
  return {
    enabled: s.enabled,
    smtp_host: s.smtp_host,
    smtp_port: String(s.smtp_port),
    smtp_security: s.smtp_security,
    smtp_username: s.smtp_username,
    password: { mode: s.smtp_password === '__SET__' ? 'unchanged' : 'empty' },
    from_address: s.from_address,
    from_display_name: s.from_display_name,
    reply_to_address: s.reply_to_address,
  };
}

function errorMessage(e: unknown): string {
  if (e && typeof e === 'object' && 'message' in e) return String((e as { message: unknown }).message);
  return 'Unknown error';
}

export function EmailSettingsTab() {
  const [status, setStatus] = useInlineStatus();
  const [config, setConfig] = useState<EmailSettings | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    settingsApi
      .getEmail()
      .then((c) => {
        if (cancelled) return;
        setConfig(c);
        setForm(formFromSettings(c));
      })
      .catch((err) => setStatus({ kind: 'error', message: errorMessage(err) }))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [setStatus]);

  const [testOpen, setTestOpen] = useState(false);
  const [testRecipient, setTestRecipient] = useState('');
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [testing, setTesting] = useState(false);

  async function handleSave() {
    if (!form || !config) return;
    const changes = diffForm(form, config);
    if (changes.length === 0) return;
    setSaving(true);
    try {
      const results = await Promise.allSettled(
        changes.map(([k, v]) => settingsApi.updateEmail(k, v)),
      );
      const failures = results
        .map((r, i) => (r.status === 'rejected' ? `${changes[i][0]}: ${errorMessage(r.reason)}` : null))
        .filter((x): x is string => Boolean(x));
      if (failures.length > 0) {
        setStatus({ kind: 'error', message: `Save failed: ${failures.join('; ')}` });
        return;
      }
      // Reload canonical state so the password field re-shows the "(saved)" placeholder.
      const fresh = await settingsApi.getEmail();
      setConfig(fresh);
      setForm(formFromSettings(fresh));
    } finally {
      setSaving(false);
    }
  }

  function diffForm(current: FormState, pristine: EmailSettings): Array<[keyof EmailSettings, unknown]> {
    const changes: Array<[keyof EmailSettings, unknown]> = [];
    if (current.enabled !== pristine.enabled) changes.push(['enabled', current.enabled]);
    if (current.smtp_host !== pristine.smtp_host) changes.push(['smtp_host', current.smtp_host]);
    if (Number(current.smtp_port) !== pristine.smtp_port) changes.push(['smtp_port', Number(current.smtp_port)]);
    if (current.smtp_security !== pristine.smtp_security) changes.push(['smtp_security', current.smtp_security]);
    if (current.smtp_username !== pristine.smtp_username) changes.push(['smtp_username', current.smtp_username]);
    if (current.from_address !== pristine.from_address) changes.push(['from_address', current.from_address]);
    if (current.from_display_name !== pristine.from_display_name) changes.push(['from_display_name', current.from_display_name]);
    if (current.reply_to_address !== pristine.reply_to_address) changes.push(['reply_to_address', current.reply_to_address]);
    // Password: only PUT on explicit user action (new / clear).
    if (current.password.mode === 'new') changes.push(['smtp_password', current.password.value]);
    else if (current.password.mode === 'clear') changes.push(['smtp_password', '']);
    return changes;
  }

  // Gate on the saved configuration (config), not the draft form (form) —
  // per spec the test endpoint uses the currently-saved config.
  //
  // Deliberately NOT gated on config.enabled: you verify the relay first, then
  // flip the master switch. The two conditions below mirror the server's
  // _is_configured() (smtp_host and from_address) by construction, so the
  // button is enabled exactly when the endpoint will accept the request.
  const canSendTest = !loading && !!config && !!config.smtp_host && !!config.from_address;

  async function handleSendTest() {
    if (!testRecipient) return;
    setTesting(true);
    setTestResult(null);
    try {
      await settingsApi.sendTestEmail(testRecipient);
      setTestResult({ ok: true, msg: 'Test email sent successfully.' });
      // Drives the Send button's green flash via successFor(status, ...),
      // the same mechanism the rest of the app uses.
      setStatus({ kind: 'success', message: 'Test email sent', action: 'send-test' });
    } catch (err) {
      // apiClient throws ApiError on non-2xx; body has { success, error }.
      const body = (err as { body?: unknown }).body as { error?: string } | undefined;
      const msg = body?.error || errorMessage(err);
      setTestResult({ ok: false, msg });
    } finally {
      setTesting(false);
    }
  }

  if (loading || !form || !config) return <LoadingSpinner />;

  const isPasswordSet = form.password.mode === 'unchanged';
  const passwordValue =
    form.password.mode === 'new' ? form.password.value
      : form.password.mode === 'clear' ? ''
      : '';

  return (
    <div className={styles.container}>
      <label className={styles.enableRow}>
        <input
          type="checkbox"
          checked={form.enabled}
          onChange={(e) => setForm((f) => f && { ...f, enabled: e.target.checked })}
        />
        Enable outbound email
      </label>

      <div className={styles.cardGrid}>
        <SettingsSection title="SMTP transport">
          <div className={styles.cardBody}>
            <div className={styles.row}>
              <label htmlFor="smtp_host">SMTP host</label>
              <input id="smtp_host" value={form.smtp_host}
                onChange={(e) => setForm((f) => f && { ...f, smtp_host: e.target.value })}
                placeholder="smtp.office365.com" />
            </div>

            <div className={styles.row}>
              <label htmlFor="smtp_port">SMTP port</label>
              <input id="smtp_port" type="number" value={form.smtp_port}
                onChange={(e) => setForm((f) => f && { ...f, smtp_port: e.target.value })} />
            </div>

            <div className={styles.row}>
              <label htmlFor="smtp_security">Security</label>
              <select id="smtp_security" value={form.smtp_security}
                onChange={(e) => setForm((f) => f && { ...f, smtp_security: e.target.value as EmailSecurity })}>
                <option value="none">none</option>
                <option value="starttls">starttls</option>
                <option value="tls">tls</option>
              </select>
            </div>

            <div className={styles.row}>
              <label htmlFor="smtp_username">Username</label>
              <input id="smtp_username" value={form.smtp_username}
                onChange={(e) => setForm((f) => f && { ...f, smtp_username: e.target.value })} />
            </div>

            <div className={styles.row}>
              <label htmlFor="smtp_password">Password</label>
              <input
                id="smtp_password"
                type="password"
                value={passwordValue}
                placeholder={isPasswordSet ? '•••••••• (saved — leave blank to keep)' : ''}
                onChange={(e) => {
                  const v = e.target.value;
                  setForm((f) => {
                    if (!f) return f;
                    // User typed a new value → 'new'.
                    if (v !== '') return { ...f, password: { mode: 'new', value: v } };
                    // User cleared the field: if a password was stored server-side
                    // (unchanged or being replaced), emit 'clear'. Only emit 'empty'
                    // when no password was ever stored.
                    const wasStored = f.password.mode === 'unchanged' || f.password.mode === 'new';
                    return { ...f, password: { mode: wasStored ? 'clear' : 'empty' } };
                  });
                }}
              />
            </div>
          </div>
        </SettingsSection>

        <SettingsSection title="Identity & links">
          <div className={styles.cardBody}>
            <div className={styles.row}>
              <label htmlFor="from_address">From address</label>
              <input id="from_address" value={form.from_address}
                onChange={(e) => setForm((f) => f && { ...f, from_address: e.target.value })}
                placeholder="no-reply@example.com" />
            </div>

            <div className={styles.row}>
              <label htmlFor="from_display_name">From display name</label>
              <input id="from_display_name" value={form.from_display_name}
                onChange={(e) => setForm((f) => f && { ...f, from_display_name: e.target.value })} />
            </div>

            <div className={styles.row}>
              <label htmlFor="reply_to_address">Reply-To (optional)</label>
              <input id="reply_to_address" value={form.reply_to_address}
                onChange={(e) => setForm((f) => f && { ...f, reply_to_address: e.target.value })} />
            </div>

            <p className={styles.hint}>
              Email links use the public AssetAtlas URL configured in
              System → Security.
            </p>
          </div>
        </SettingsSection>
      </div>

      <div className={styles.actions}>
        {status?.kind === 'error' && <InlineStatus status={status} align="inline" />}
        <Button
          variant="primary"
          onClick={() => { setTestOpen(true); setTestResult(null); setTestRecipient(''); }}
          disabled={!canSendTest}
        >
          Send test email
        </Button>
        <Button variant="secondary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </div>

      <Modal
        isOpen={testOpen}
        onClose={() => setTestOpen(false)}
        title="Send test email"
        size="sm"
      >
        <div className={styles.row}>
          <label htmlFor="test_recipient">Recipient</label>
          <input
            id="test_recipient"
            value={testRecipient}
            onChange={(e) => setTestRecipient(e.target.value)}
            placeholder="admin@example.com"
          />
        </div>
        {/* Failures still need their text: the send can fail a dozen ways
            (auth, TLS, relay refused) and the reason is the whole point.
            Success needs no line — the button's own flash says it. */}
        {testResult && !testResult.ok && (
          <div role="status" className={styles.failure}>
            {testResult.msg}
          </div>
        )}
        <div className={styles.actions}>
          <Button
            onClick={handleSendTest}
            disabled={!testRecipient}
            loading={testing}
            success={successFor(status, 'send-test')}
            successLabel="Sent"
          >
            Send
          </Button>
          <Button variant="secondary" onClick={() => setTestOpen(false)}>
            Close
          </Button>
        </div>
      </Modal>
    </div>
  );
}
