// SPDX-License-Identifier: AGPL-3.0-or-later
// Operator-facing UI for the Microsoft Teams chatbot integration. Lets
// admins toggle the channel, paste their Azure Bot credentials, copy the
// messaging endpoint into Azure Portal, probe the on-screen credentials
// via the bridge's /test-credentials, and download the Teams app manifest
// zip for sideloading.
//
// Per-field saves on blur (mirrors how settingsApi.updateEmail is wired,
// but skipping the explicit Save button since each field is independent).
// The client secret is treated specially via a discriminated union so a
// stray focus on the field cannot silently wipe the stored value: only
// 'new' triggers a PUT, and only after the user clicks Save.
//
// The master switch commits on click for the same reason (#1227). LDAP and
// OIDC commit theirs with the card's Save because they are draft forms and a
// click-commit would half-commit the rest; this tab has no draft to strand,
// so a Save-committed switch would be the one control here that doesn't do
// what the others do. The rule across the four master toggles is therefore
// "the switch commits the way its tab commits", and the note beneath it says
// which that is: it only carries "Not applied until you press Save." when
// something on screen really is unsaved.
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FocusEvent } from 'react';

import {
  teamsApi,
  type MessagingEndpoint,
  type TeamsSettings,
  type TeamsSettingsKey,
  type TestConnectionResult,
} from '@/api/teams';
import { Button } from '@/components/ui/Button';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { InlineStatus, successFor, useInlineStatus } from '@/components/ui/InlineStatus';
import { EyeIcon } from '@/components/ui/EyeIcon';
// Same helper CategorySettings uses for the LDAP/OIDC master-toggle note, so
// the notes read identically wherever they are rendered.
import { joinLabels } from '@/utils/formatters';
import styles from './ChatbotTeamsTab.module.css';

const UUID_RE =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

// Discriminated union — see notes above and the email tab for the pattern.
type SecretDraft =
  | { mode: 'unchanged' }            // a secret is stored on the server
  | { mode: 'empty' }                // no secret stored, no input yet
  | { mode: 'new'; value: string };  // user typed a new secret

interface FormState {
  enabled: boolean;
  bot_app_id: string;
  bot_app_tenant_id: string;
  messaging_endpoint: string;
  secret: SecretDraft;
}

function formFromSettings(s: TeamsSettings): FormState {
  return {
    enabled: s.enabled,
    bot_app_id: s.bot_app_id,
    bot_app_tenant_id: s.bot_app_tenant_id,
    messaging_endpoint: s.messaging_endpoint || '',
    secret: { mode: s.bot_app_password === '__SET__' ? 'unchanged' : 'empty' },
  };
}

function errorMessage(e: unknown): string {
  if (e && typeof e === 'object' && 'message' in e) {
    return String((e as { message: unknown }).message);
  }
  return 'Unknown error';
}

function isUuid(v: string): boolean {
  return UUID_RE.test(v);
}

export function ChatbotTeamsTab() {
  const [status, setStatus] = useInlineStatus();
  const [config, setConfig] = useState<TeamsSettings | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [endpoint, setEndpoint] = useState<MessagingEndpoint | null>(null);
  const [testResult, setTestResult] = useState<TestConnectionResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const [revealSecret, setRevealSecret] = useState(false);

  // Token for the credential set currently on screen. onTest captures it
  // before probing; invalidateTestResult bumps it, so a verdict that lands
  // after a credential edit is discarded instead of resurfacing stale.
  const testSeq = useRef(0);

  // A test result describes the credentials it ran against — drop it (and
  // supersede any in-flight probe) whenever one of them changes. The
  // Configuration tab centralises the same invalidation in setProviderDraft.
  const invalidateTestResult = () => {
    testSeq.current += 1;
    setTestResult(null);
  };

  useEffect(() => {
    let cancelled = false;
    Promise.all([teamsApi.get(), teamsApi.getMessagingEndpoint()])
      .then(([s, ep]) => {
        if (cancelled) return;
        setConfig(s);
        setForm(formFromSettings(s));
        setEndpoint(ep);
      })
      .catch((err) =>
        setStatus({ kind: 'error', message: errorMessage(err) }),
      )
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [setStatus]);

  // Returns whether the PUT landed, so a caller that moved the form
  // optimistically can put it back.
  const updateField = useCallback(
    async (key: TeamsSettingsKey, value: unknown): Promise<boolean> => {
      try {
        await teamsApi.update(key, value);
        return true;
      } catch (err) {
        setStatus({ kind: 'error', message: errorMessage(err) });
        return false;
      }
    },
    [setStatus],
  );

  const onToggleEnabled = async (next: boolean) => {
    if (!form) return;
    setForm({ ...form, enabled: next });
    // The note under the switch says "already applied" by saying nothing, so
    // a rejected PUT must not leave a flipped switch and a present-tense note
    // describing a state the server never took.
    const ok = await updateField('enabled', next);
    if (!ok) {
      setForm((prev) => (prev ? { ...prev, enabled: !next } : prev));
    }
  };

  const onIdBlur = async (e: FocusEvent<HTMLInputElement>) => {
    if (!form) return;
    const v = e.target.value.trim();
    if (v !== '' && !isUuid(v)) {
      // The (uncontrolled) input now shows text the form never accepted —
      // any shown verdict no longer describes what is on screen.
      invalidateTestResult();
      setStatus({
        kind: 'error',
        message: 'Application (client) ID must be a UUID',
      });
      return;
    }
    if (v === form.bot_app_id) return;
    // A test result describes the credentials it ran against — drop it when
    // any of them changes (the Configuration tab does the same via testOk).
    invalidateTestResult();
    setForm({ ...form, bot_app_id: v });
    await updateField('bot_app_id', v);
  };

  const onTenantBlur = async (e: FocusEvent<HTMLInputElement>) => {
    if (!form) return;
    const v = e.target.value.trim();
    if (v !== '' && !isUuid(v)) {
      invalidateTestResult();
      setStatus({ kind: 'error', message: 'Tenant ID must be a UUID' });
      return;
    }
    if (v === form.bot_app_tenant_id) return;
    invalidateTestResult();
    setForm({ ...form, bot_app_tenant_id: v });
    await updateField('bot_app_tenant_id', v);
  };

  const onMessagingEndpointBlur = async (e: FocusEvent<HTMLInputElement>) => {
    if (!form) return;
    const v = e.target.value.trim();
    if (v !== '' && !v.toLowerCase().startsWith('https://')) {
      setStatus({
        kind: 'error',
        message: 'messaging_endpoint must be HTTPS or empty',
      });
      return;
    }
    if (v === form.messaging_endpoint) return;
    setForm({ ...form, messaging_endpoint: v });
    await updateField('messaging_endpoint', v);
    try {
      setEndpoint(await teamsApi.getMessagingEndpoint());
    } catch (err) {
      setStatus({ kind: 'error', message: errorMessage(err) });
    }
  };

  const onSecretSave = async () => {
    if (!form || !config || form.secret.mode !== 'new') return;
    try {
      await teamsApi.update('bot_app_password', form.secret.value);
      // Keep any test result: typing cleared stale ones, so what remains
      // was run against exactly the value being saved.
      setForm({ ...form, secret: { mode: 'unchanged' } });
      setRevealSecret(false);
      // A secret is now stored: reflect that locally even if the refresh
      // below fails, so Cancel's stored-vs-empty decision cannot go stale.
      setConfig({ ...config, bot_app_password: '__SET__' });
      const fresh = await teamsApi.get();
      setConfig(fresh);
      setStatus({ kind: 'success', message: 'Client secret saved' });
    } catch (err) {
      setStatus({ kind: 'error', message: errorMessage(err) });
    }
  };

  const onCopyEndpoint = () => {
    if (!endpoint?.url) return;
    void navigator.clipboard.writeText(endpoint.url);
    setStatus({ kind: 'success', message: 'Copied', action: 'copy' });
  };

  const onTest = async () => {
    if (!form) return;
    // A verdict is valid only for the credentials captured now: if an edit
    // bumps testSeq while the probe is in flight, discard the late result
    // instead of resurfacing it next to values it never ran against.
    const seq = ++testSeq.current;
    setTesting(true);
    setTestResult(null);
    try {
      // Probe what is on screen, as the Configuration tab does: a typed
      // (unsaved) secret rides in the body; fields left out fall back to
      // the stored values server-side (#1226).
      const result = await teamsApi.testConnection({
        bot_app_id: form.bot_app_id,
        bot_app_tenant_id: form.bot_app_tenant_id,
        ...(form.secret.mode === 'new'
          ? { bot_app_password: form.secret.value }
          : {}),
      });
      if (seq === testSeq.current) setTestResult(result);
    } catch (err) {
      const body = (err as { body?: unknown }).body as
        | { error?: string }
        | undefined;
      if (seq === testSeq.current) {
        setTestResult({ ok: false, error: body?.error || errorMessage(err) });
      }
    } finally {
      setTesting(false);
    }
  };

  const onDownload = async () => {
    setDownloading(true);
    try {
      const { blob, filename } = await teamsApi.downloadManifest();
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(objectUrl);
    } catch (err) {
      setStatus({ kind: 'error', message: errorMessage(err) });
    } finally {
      setDownloading(false);
    }
  };

  if (loading || !form || !config) return <LoadingSpinner />;

  // 'new' needs an actual value: with the box empty there is nothing to
  // send, and the test would fall back to the stored secret — the exact
  // mismatch #1226 removes.
  const secretOnScreen =
    form.secret.mode === 'unchanged' ||
    (form.secret.mode === 'new' && form.secret.value.trim() !== '');
  // Kept as the list of what is *missing* rather than a bare boolean: the
  // buttons and the note under the switch both read it, so they cannot
  // disagree about what "complete" means (the #1149 failure mode).
  const missingCreds: string[] = [];
  if (!isUuid(form.bot_app_id)) missingCreds.push('Application (client) ID');
  if (!secretOnScreen) missingCreds.push('Client secret');
  if (!isUuid(form.bot_app_tenant_id)) missingCreds.push('Tenant ID');

  const credsComplete = missingCreds.length === 0;
  const httpsOk = endpoint?.https_ok ?? false;
  const downloadEnabled = form.enabled && credsComplete && httpsOk;
  const testEnabled = form.enabled && credsComplete && httpsOk && !testing;

  // Effective state of the master switch, in the shape CategorySettings gives
  // the LDAP/OIDC master toggles (#1149, #1227). Derived from exactly what
  // gates the buttons: an "on" switch next to two greyed-out buttons was the
  // whole complaint, so the note must never be able to claim more than they do.
  const blockers: string[] = [];
  if (missingCreds.length > 0) {
    blockers.push(`${joinLabels(missingCreds)} not configured`);
  }
  if (!httpsOk) blockers.push('the public URL is not HTTPS');
  const noteWarn = form.enabled && blockers.length > 0;
  let noteText: string;
  if (!form.enabled) {
    noteText =
      'Disabled — the bridge is idle; messages sent in Teams go unanswered.';
  } else if (blockers.length > 0) {
    noteText = `Enabled, but incomplete — ${blockers.join('; ')}.`;
  } else {
    noteText = 'Enabled — the bridge answers messages in Microsoft Teams.';
  }
  // The note describes what is on screen, exactly as the buttons do. The one
  // value that can be on screen without being stored is a typed secret, so
  // borrow CategorySettings' affordance and point at its Save. The switch
  // itself never needs the clause — it commits on click, like every other
  // field on this tab — so "no clause" reads as "already applied".
  const secretPending =
    form.secret.mode === 'new' && form.secret.value.trim() !== '';
  if (form.enabled && secretPending) {
    noteText = `${noteText} Not applied until you press Save.`;
  }

  return (
    <div className={styles.container}>
      <div className={styles.enableRow}>
        <input
          id="teams-enabled"
          type="checkbox"
          checked={form.enabled}
          onChange={(e) => void onToggleEnabled(e.target.checked)}
        />
        <span className={styles.enableText}>
          <label htmlFor="teams-enabled" className={styles.enableLabel}>
            Enable Microsoft Teams chatbot
          </label>
          <span
            className={`${styles.enableNote} ${
              noteWarn ? styles.enableWarn : ''
            }`}
          >
            {noteText}
          </span>
        </span>
      </div>

      <div className={styles.contentGrid}>
        {/* ── Left: config card ─────────────────────────────────── */}
        <div className={styles.configCard}>
          <section className={styles.cardSection}>
            <h3 className={styles.sectionTitle}>Azure Bot credentials</h3>

            <div className={styles.row}>
              <label htmlFor="teams-app-id">Application (client) ID</label>
              <input
                id="teams-app-id"
                type="text"
                defaultValue={form.bot_app_id}
                placeholder="11111111-1111-1111-1111-111111111111"
                onBlur={onIdBlur}
                disabled={!form.enabled}
              />
            </div>

            <div className={styles.row}>
              <label htmlFor="teams-secret">Client secret</label>
              {form.secret.mode === 'new' ? (
                <div className={styles.secretRow}>
                  <div className={styles.secretInputWrap}>
                    <input
                      id="teams-secret"
                      type={revealSecret ? 'text' : 'password'}
                      className={styles.secretInputWithEye}
                      autoFocus
                      placeholder="Paste the Azure Bot client secret"
                      value={form.secret.value}
                      onChange={(e) => {
                        invalidateTestResult();
                        setForm({
                          ...form,
                          secret: { mode: 'new', value: e.target.value },
                        });
                      }}
                    />
                    <button
                      type="button"
                      className={styles.secretEyeBtn}
                      title={revealSecret ? 'Hide secret' : 'Show secret'}
                      aria-label={revealSecret ? 'Hide secret' : 'Show secret'}
                      onClick={() => setRevealSecret((v) => !v)}
                    >
                      <EyeIcon size={16} open={!revealSecret} />
                    </button>
                  </div>
                  <button
                    type="button"
                    className={styles.iconBtn}
                    title="Cancel"
                    aria-label="Cancel"
                    onClick={() => {
                      setRevealSecret(false);
                      invalidateTestResult();
                      setForm({
                        ...form,
                        secret: {
                          mode:
                            config.bot_app_password === '__SET__'
                              ? 'unchanged'
                              : 'empty',
                        },
                      });
                    }}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="18" y1="6" x2="6" y2="18" />
                      <line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  </button>
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => void onSecretSave()}
                    // Trimmed, matching credsComplete: a whitespace-only
                    // secret that Test refuses must not be saveable either.
                    disabled={!form.secret.value.trim()}
                  >
                    Save
                  </Button>
                </div>
              ) : (
                <div className={styles.secretRow}>
                  <div className={styles.secretPreview}>
                    {form.secret.mode === 'unchanged' ? (
                      <code>•••••••• (saved)</code>
                    ) : (
                      <span className={styles.secretPreviewEmpty}>Not configured</span>
                    )}
                  </div>
                  <button
                    type="button"
                    className={styles.iconBtn}
                    title="Edit client secret"
                    aria-label="Edit client secret"
                    disabled={!form.enabled}
                    onClick={() => {
                      invalidateTestResult();
                      setForm({ ...form, secret: { mode: 'new', value: '' } });
                    }}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                    </svg>
                  </button>
                </div>
              )}
            </div>

            <div className={styles.row}>
              <label htmlFor="teams-tenant-id">Tenant ID</label>
              <input
                id="teams-tenant-id"
                type="text"
                defaultValue={form.bot_app_tenant_id}
                placeholder="22222222-2222-2222-2222-222222222222"
                onBlur={onTenantBlur}
                disabled={!form.enabled}
              />
            </div>
          </section>

          <section className={styles.cardSection}>
            <h3 className={styles.sectionTitle}>Messaging endpoint</h3>
            <p className={styles.hint}>
              Paste this URL into Azure Portal → your Azure Bot →
              Configuration → Messaging endpoint.
            </p>
            <div className={styles.endpointRow}>
              <input
                type="text"
                defaultValue={form.messaging_endpoint}
                placeholder={endpoint?.url ?? ''}
                onBlur={onMessagingEndpointBlur}
                className={styles.endpointInput}
                aria-label="Messaging endpoint URL"
                disabled={!form.enabled}
              />
              <Button
                size="sm"
                variant="secondary"
                onClick={onCopyEndpoint}
                disabled={!endpoint?.url}
                success={successFor(status, 'copy')}
              >
                Copy
              </Button>
            </div>
            {form.messaging_endpoint ? (
              <p className={styles.hint}>
                Override active — using the URL above as-is.
              </p>
            ) : (
              <p className={styles.hint}>
                Empty = computed from the public AssetAtlas URL configured in
                System &rarr; Security; clear the field to revert.
              </p>
            )}
            {endpoint && !endpoint.https_ok && (
              <p className={styles.warning}>
                Public URL must be HTTPS for Teams to deliver messages.
                Update it in System &rarr; Security.
              </p>
            )}
          </section>

          <div className={styles.cardActions}>
            {status?.kind === 'error' && (
              <InlineStatus status={status} align="inline" />
            )}
            <Button
              size="sm"
              variant="secondary"
              onClick={() => void onTest()}
              disabled={!testEnabled}
            >
              {testing ? 'Testing…' : 'Test connection'}
            </Button>
            <Button
              size="sm"
              variant="primary"
              onClick={() => void onDownload()}
              disabled={!downloadEnabled || downloading}
            >
              {downloading ? 'Preparing…' : 'Download Teams app'}
            </Button>
          </div>

          {testResult && (
            <p
              className={testResult.ok ? styles.success : styles.failure}
              role="status"
            >
              {testResult.ok
                ? 'Connection ok — Bot Framework accepted the credentials.'
                : `Connection failed: ${testResult.error || 'unknown error'}`}
            </p>
          )}
        </div>

        {/* ── Right: how-to card ────────────────────────────────── */}
        <aside className={styles.howtoCard}>
          <h3 className={styles.sectionTitle}>How to set up the Teams chatbot</h3>
          <ol className={styles.howtoList}>
            <li>
              Azure portal → Create resource → <b>Azure Bot</b> (Free F0,
              Single Tenant).
            </li>
            <li>
              Capture the App ID, generate a client secret, note the Tenant ID.
              Paste them into the card on the left.
            </li>
            <li>
              Copy the messaging endpoint into Azure Bot → Configuration.
            </li>
            <li>
              In the Azure Bot resource, add the <b>Microsoft Teams</b> channel.
            </li>
            <li>
              Click <b>Download Teams app</b> and sideload via Teams Admin
              Center → Manage apps → Upload custom app.
            </li>
          </ol>
        </aside>
      </div>
    </div>
  );
}
