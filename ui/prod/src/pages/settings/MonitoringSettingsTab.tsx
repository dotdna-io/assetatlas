// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/MonitoringSettingsTab.tsx
//
// Settings -> Monitoring -> Forwarding (#922 Phase 3): admin config for the
// remote_write target the bundled Prometheus-agent forwards AssetAtlas metrics
// to. A singleton-config form (load -> edit -> Save/Test), mirroring
// EmailSettingsTab. The remote_write secret is WRITE-ONLY: a stored secret shows
// as a "(saved)" placeholder and is only sent when the admin types a new value
// (blank keeps it; auth_type "none" clears it). Save surfaces the backend's
// apply/reload status; Test checks the currently-saved endpoint's reachability.
//
// Admin-gating is inherited from SettingsPage (the whole /settings tree is
// admin-only), matching how EmailSettingsTab / AlertChannelsTab are gated.

import { useEffect, useState } from 'react';

import {
  metricsForwardingApi,
  type ForwardingApplyStatus,
  type ForwardingAuthType,
  type ForwardingConfig,
  type ForwardingConfigUpdate,
} from '@/api/metricsForwarding';
import { getApiErrorMessage } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { InlineStatus, type InlineStatusValue } from '@/components/ui/InlineStatus';
import { SettingsSection } from '@/components/settings';
import styles from './MonitoringSettingsTab.module.css';

const SECRET_PLACEHOLDER = '•••••••• (saved — leave blank to keep)';

// Discriminated union so a stray render can't silently wipe a stored secret —
// only 'new' is ever sent; 'unchanged' means "keep what the server has", 'empty'
// means "nothing stored, none entered". Same idiom as WebhookChannelForm /
// EmailSettingsTab.
type SecretDraft =
  | { mode: 'unchanged' }
  | { mode: 'new'; value: string }
  | { mode: 'empty' };

interface LabelRow {
  key: string;
  value: string;
}

interface FormState {
  enabled: boolean;
  endpointUrl: string;
  authType: ForwardingAuthType;
  username: string;
  secret: SecretDraft;
  labels: LabelRow[];
}

function formFromConfig(config: ForwardingConfig): FormState {
  return {
    enabled: config.enabled,
    endpointUrl: config.endpoint_url ?? '',
    authType: config.auth_type,
    username: config.username ?? '',
    secret: config.has_secret ? { mode: 'unchanged' } : { mode: 'empty' },
    labels: Object.entries(config.external_labels ?? {}).map(([key, value]) => ({
      key,
      value: String(value),
    })),
  };
}

function buildUpdate(form: FormState): ForwardingConfigUpdate {
  const external_labels: Record<string, string> = {};
  for (const { key, value } of form.labels) {
    const k = key.trim();
    if (k) external_labels[k] = value; // last write wins on duplicate keys
  }
  const body: ForwardingConfigUpdate = {
    endpoint_url: form.endpointUrl.trim() || null,
    auth_type: form.authType,
    external_labels,
    enabled: form.enabled,
  };
  if (form.authType === 'basic') {
    body.username = form.username.trim() || null;
  }
  // Write-only: only send the secret when the admin typed a new one. Omit it for
  // 'unchanged'/'empty' so the backend keeps the stored value; 'none' clears it.
  if (form.authType !== 'none' && form.secret.mode === 'new') {
    body.auth_secret = form.secret.value;
  }
  return body;
}

// Map the backend's apply/reload block to an admin-facing banner. A clean save
// shows nothing (null) — the form quietly reflects the saved state; only a
// problem the admin must act on surfaces: a failed config write (error) or an
// unreachable agent (the change hasn't taken effect yet).
function applyMessage(apply: ForwardingApplyStatus | undefined): InlineStatusValue | null {
  if (!apply) return null;
  if (!apply.enabled) return null;
  if (apply.config_written === false) {
    return {
      kind: 'error',
      message:
        'Saved, but writing the agent config failed' +
        (apply.write_error ? `: ${apply.write_error}` : '') +
        '. Forwarding will not apply until this is resolved.',
    };
  }
  if (apply.reload?.ok) return null;
  return {
    kind: 'error',
    message:
      'Saved, but the forwarding agent is unreachable — the new configuration applies on its next start.',
  };
}

export function MonitoringSettingsTab() {
  const [config, setConfig] = useState<ForwardingConfig | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [status, setStatus] = useState<InlineStatusValue | null>(null);

  useEffect(() => {
    let cancelled = false;
    metricsForwardingApi
      .getConfig()
      .then((c) => {
        if (cancelled) return;
        setConfig(c);
        setForm(formFromConfig(c));
      })
      .catch((err) => {
        if (!cancelled) {
          setStatus({ kind: 'error', message: getApiErrorMessage(err, 'Failed to load configuration') });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function update(patch: Partial<FormState>) {
    setForm((f) => (f ? { ...f, ...patch } : f));
  }

  // `has_secret` on the pristine loaded config is fixed for the form's lifetime,
  // so clearing the field back to blank reverts to the correct placeholder state.
  function handleSecretChange(v: string) {
    if (v === '') {
      update({ secret: config?.has_secret ? { mode: 'unchanged' } : { mode: 'empty' } });
    } else {
      update({ secret: { mode: 'new', value: v } });
    }
  }

  function updateLabel(index: number, patch: Partial<LabelRow>) {
    setForm((f) =>
      f ? { ...f, labels: f.labels.map((l, i) => (i === index ? { ...l, ...patch } : l)) } : f,
    );
  }
  function addLabel() {
    setForm((f) => (f ? { ...f, labels: [...f.labels, { key: '', value: '' }] } : f));
  }
  function removeLabel(index: number) {
    setForm((f) => (f ? { ...f, labels: f.labels.filter((_, i) => i !== index) } : f));
  }

  async function handleSave() {
    if (!form) return;
    // Client-side guard for the one rule that blocks a useful save immediately;
    // the backend re-validates completeness (secret/username) and its 400 message
    // is surfaced via the catch below.
    if (form.enabled && !form.endpointUrl.trim()) {
      setStatus({ kind: 'error', message: 'Endpoint URL is required to enable forwarding.' });
      return;
    }
    setSaving(true);
    setStatus(null);
    try {
      const { apply, ...view } = await metricsForwardingApi.saveConfig(buildUpdate(form));
      setConfig(view);
      setForm(formFromConfig(view));
      // Clean save is silent; only genuine problems surface (see applyMessage).
      setStatus(applyMessage(apply));
    } catch (err) {
      setStatus({ kind: 'error', message: getApiErrorMessage(err, 'Failed to save configuration') });
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    setStatus(null);
    try {
      const res = await metricsForwardingApi.testConnection();
      setStatus(
        res.reachable
          ? {
              kind: 'success',
              message:
                'Endpoint reachable' +
                (res.status_code != null ? ` — HTTP ${res.status_code}` : '') +
                '.',
            }
          : { kind: 'error', message: res.detail || res.error || 'Endpoint not reachable.' },
      );
    } catch (err) {
      setStatus({ kind: 'error', message: getApiErrorMessage(err, 'Test failed') });
    } finally {
      setTesting(false);
    }
  }

  if (loading || !form || !config) return <LoadingSpinner />;

  const secretStored = form.secret.mode === 'unchanged';
  const secretValue = form.secret.mode === 'new' ? form.secret.value : '';

  return (
    <div className={styles.container}>
      <label className={styles.enableRow}>
        <input
          type="checkbox"
          checked={form.enabled}
          onChange={(e) => update({ enabled: e.target.checked })}
        />
        Enable metrics forwarding
      </label>

      <SettingsSection
        title="Remote write target"
        subtitle="Forward AssetAtlas metrics to a Prometheus-compatible endpoint (Grafana, Mimir, Thanos, VictoriaMetrics)."
      >
        <div className={styles.cardBody}>
          <div className={styles.row}>
            <label htmlFor="mf_endpoint">Endpoint URL</label>
            <input
              id="mf_endpoint"
              value={form.endpointUrl}
              onChange={(e) => update({ endpointUrl: e.target.value })}
              placeholder="https://prometheus.example.com/api/v1/write"
            />
          </div>

          <div className={styles.row}>
            <label htmlFor="mf_auth_type">Authentication</label>
            <select
              id="mf_auth_type"
              value={form.authType}
              onChange={(e) => update({ authType: e.target.value as ForwardingAuthType })}
            >
              <option value="none">None</option>
              <option value="bearer">Bearer token</option>
              <option value="basic">Basic auth</option>
            </select>
          </div>

          {form.authType === 'bearer' && (
            <div className={styles.row}>
              <label htmlFor="mf_token">Bearer token</label>
              <input
                id="mf_token"
                type="password"
                autoComplete="off"
                value={secretValue}
                placeholder={secretStored ? SECRET_PLACEHOLDER : ''}
                onChange={(e) => handleSecretChange(e.target.value)}
              />
            </div>
          )}

          {form.authType === 'basic' && (
            <>
              <div className={styles.row}>
                <label htmlFor="mf_username">Username</label>
                <input
                  id="mf_username"
                  autoComplete="off"
                  value={form.username}
                  onChange={(e) => update({ username: e.target.value })}
                />
              </div>
              <div className={styles.row}>
                <label htmlFor="mf_password">Password</label>
                <input
                  id="mf_password"
                  type="password"
                  autoComplete="off"
                  value={secretValue}
                  placeholder={secretStored ? SECRET_PLACEHOLDER : ''}
                  onChange={(e) => handleSecretChange(e.target.value)}
                />
              </div>
            </>
          )}
        </div>
      </SettingsSection>

      <SettingsSection
        title="External labels"
        subtitle="Labels attached to every forwarded series (e.g. cluster, environment)."
      >
        <div className={styles.cardBody}>
          {form.labels.length === 0 ? (
            <p className={styles.emptyLabels}>No external labels.</p>
          ) : (
            <div className={styles.labelList}>
              {form.labels.map((label, i) => (
                <div key={i} className={styles.labelRow}>
                  <input
                    aria-label={`Label ${i + 1} name`}
                    placeholder="name"
                    value={label.key}
                    onChange={(e) => updateLabel(i, { key: e.target.value })}
                  />
                  <input
                    aria-label={`Label ${i + 1} value`}
                    placeholder="value"
                    value={label.value}
                    onChange={(e) => updateLabel(i, { value: e.target.value })}
                  />
                  <button
                    type="button"
                    className={styles.removeBtn}
                    aria-label={`Remove label ${i + 1}`}
                    onClick={() => removeLabel(i)}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
          <button type="button" className={styles.addBtn} onClick={addLabel}>
            + Add label
          </button>
        </div>
      </SettingsSection>

      <p className={styles.hint}>“Test connection” checks the currently-saved endpoint, not unsaved edits.</p>

      <div className={styles.actions}>
        {status && <InlineStatus status={status} align="inline" />}
        <Button
          variant="secondary"
          onClick={handleTest}
          disabled={testing || !config.endpoint_url}
          loading={testing}
        >
          {testing ? 'Testing…' : 'Test connection'}
        </Button>
        <Button variant="primary" onClick={handleSave} disabled={saving} loading={saving}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  );
}
