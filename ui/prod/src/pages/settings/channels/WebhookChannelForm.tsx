// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';

import { isSecretConfigured, type AlertChannel } from '@/api/channels';
import { SettingsSection } from '@/components/settings';
import styles from './WebhookChannelForm.module.css';

// Mirrors EmailSettingsTab's PasswordDraft: a discriminated union so a
// stray render can't silently wipe a stored secret. Only 'new' causes the
// field to be included in the emitted config; 'unchanged' means "keep
// whatever the server already has" and 'empty' means "nothing stored, none
// entered".
type SecretDraft =
  | { mode: 'unchanged' }
  | { mode: 'new'; value: string }
  | { mode: 'empty' };

interface FormState {
  url: string;
  timeoutText: string; // kept as string for <input type="number">
  signingKey: SecretDraft;
  headers: SecretDraft; // 'new' variant's value is the raw JSON textarea text
}

interface WebhookChannelFormProps {
  channel?: AlertChannel;
  onConfigChange: (config: Record<string, unknown>) => void;
}

function configString(channel: AlertChannel | undefined, field: string): string {
  const v = channel?.config?.[field];
  return typeof v === 'string' ? v : '';
}

function configTimeout(channel: AlertChannel | undefined): string {
  const v = channel?.config?.timeout;
  return typeof v === 'number' ? String(v) : '30';
}

function initialSecretDraft(channel: AlertChannel | undefined, field: string): SecretDraft {
  return channel && isSecretConfigured(channel.config[field])
    ? { mode: 'unchanged' }
    : { mode: 'empty' };
}

// Number('') is 0, which is finite — Number.isFinite alone can't tell a
// blank field from an explicit 0, so check for blank first. This keeps an
// explicit "0" from being silently coerced up to the 30s default.
function parseTimeout(text: string): number {
  const n = Number(text);
  return text.trim() !== '' && Number.isFinite(n) ? n : 30;
}

function initialFormState(channel: AlertChannel | undefined): FormState {
  return {
    url: configString(channel, 'url'),
    timeoutText: configTimeout(channel),
    signingKey: initialSecretDraft(channel, 'signing_key'),
    headers: initialSecretDraft(channel, 'headers'),
  };
}

/** Builds the config object to emit: url/timeout always, signing_key/headers
 *  only when their draft was actually edited this session. Returns a headers
 *  parse error message (or null) so the caller can render it — invalid JSON
 *  headers are never included in the emitted config. */
function buildConfig(form: FormState): { config: Record<string, unknown>; headersError: string | null } {
  const config: Record<string, unknown> = {
    url: form.url,
    timeout: parseTimeout(form.timeoutText),
  };
  if (form.signingKey.mode === 'new') config.signing_key = form.signingKey.value;

  let headersError: string | null = null;
  if (form.headers.mode === 'new') {
    try {
      const parsed = JSON.parse(form.headers.value);
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        headersError = 'Headers must be a JSON object';
      } else if (Object.entries(parsed).some(([k, v]) => typeof k !== 'string' || typeof v !== 'string')) {
        // Backend parity: WebhookChannel.validate_config requires a flat object of
        // string keys AND string values (e.g. {"X-Api-Key": 123} 400s server-side).
        headersError = 'Headers must be a flat object of string keys and values';
      } else {
        config.headers = parsed;
      }
    } catch {
      headersError = 'Invalid JSON';
    }
  }
  return { config, headersError };
}

export function WebhookChannelForm({ channel, onConfigChange }: WebhookChannelFormProps) {
  const [form, setForm] = useState<FormState>(() => initialFormState(channel));
  const [headersError, setHeadersError] = useState<string | null>(null);

  function update(patch: Partial<FormState>) {
    const next = { ...form, ...patch };
    setForm(next);
    const { config, headersError: err } = buildConfig(next);
    setHeadersError(err);
    onConfigChange(config);
  }

  // Whether a secret was already stored on the channel as loaded — fixed for
  // the lifetime of this form, independent of the current draft, so clearing
  // a field back to empty reverts to the correct placeholder state.
  const wasSigningKeyStored = !!channel && isSecretConfigured(channel.config['signing_key']);
  const wasHeadersStored = !!channel && isSecretConfigured(channel.config['headers']);

  const signingKeyStored = form.signingKey.mode === 'unchanged';
  const signingKeyValue = form.signingKey.mode === 'new' ? form.signingKey.value : '';

  const headersStored = form.headers.mode === 'unchanged';
  const headersValue = form.headers.mode === 'new' ? form.headers.value : '';

  function handleSigningKeyChange(v: string) {
    if (v === '') {
      update({ signingKey: wasSigningKeyStored ? { mode: 'unchanged' } : { mode: 'empty' } });
      return;
    }
    update({ signingKey: { mode: 'new', value: v } });
  }

  function handleHeadersChange(v: string) {
    if (v === '') {
      update({ headers: wasHeadersStored ? { mode: 'unchanged' } : { mode: 'empty' } });
      return;
    }
    update({ headers: { mode: 'new', value: v } });
  }

  return (
    <SettingsSection title="Webhook configuration">
      <div className={styles.cardBody}>
        <div className={styles.row}>
          <label htmlFor="webhook_url">URL</label>
          <input
            id="webhook_url"
            value={form.url}
            onChange={(e) => update({ url: e.target.value })}
            placeholder="https://hooks.example.com/incoming"
          />
        </div>

        <div className={styles.row}>
          <label htmlFor="webhook_timeout">Timeout (seconds)</label>
          <input
            id="webhook_timeout"
            type="number"
            value={form.timeoutText}
            onChange={(e) => update({ timeoutText: e.target.value })}
          />
        </div>

        <div className={styles.row}>
          <label htmlFor="webhook_signing_key">Signing key</label>
          <input
            id="webhook_signing_key"
            type="password"
            value={signingKeyValue}
            placeholder={signingKeyStored ? '•••••••• (saved — leave blank to keep)' : ''}
            onChange={(e) => handleSigningKeyChange(e.target.value)}
          />
        </div>

        <div className={styles.row}>
          <label htmlFor="webhook_headers">Headers (JSON)</label>
          <textarea
            id="webhook_headers"
            className={styles.textarea}
            value={headersValue}
            placeholder={headersStored
              ? '(saved — leave blank to keep)'
              : '{"X-Api-Key": "..."}'}
            onChange={(e) => handleHeadersChange(e.target.value)}
          />
          {headersError && <div className={styles.error}>{headersError}</div>}
        </div>
      </div>
    </SettingsSection>
  );
}
