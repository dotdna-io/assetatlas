// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/Button';
import { EyeIcon } from '@/components/ui/EyeIcon';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import type { WizardFormData, WizardMode, CustomHttpConfig } from './wizard-state';
import { DEFAULT_CUSTOM_HTTP_CONFIG } from './wizard-state';
import type { CustomHttpTestResponse } from '@/api/integrations';
import { integrationsApi } from '@/api/integrations';
import styles from './CustomHttpConfigStep.module.css';

interface Props {
  formData: WizardFormData;
  onUpdate: (partial: Partial<WizardFormData>) => void;
  mode: WizardMode;
  // When the parent owns test state (used by the 50/50 ConnectionStep layout
  // for custom_http: button + output live on the right column), pass these in
  // and set hideTestSection to skip the inline preview block.
  testing?: boolean;
  testResult?: CustomHttpTestResponse | null;
  onTest?: () => void;
  hideTestSection?: boolean;
  // Lets ConnectionStep split this component across two columns — 'left'
  // renders only the connection setup (Endpoint/Auth/Pagination/Body),
  // 'right' renders the response-shaping sections (Output, Records root,
  // Field bindings). Omit for the legacy single-column rendering.
  placement?: 'left' | 'right';
}

export function CustomHttpConfigStep({
  formData, onUpdate,
  testing: externalTesting,
  testResult: externalTestResult,
  onTest: externalOnTest,
  hideTestSection,
  placement,
}: Props) {
  const cfg = (formData.config as Partial<CustomHttpConfig>) ?? {};
  const merged: CustomHttpConfig = { ...DEFAULT_CUSTOM_HTTP_CONFIG, ...cfg };

  const update = (patch: Partial<CustomHttpConfig>) => {
    onUpdate({ config: { ...merged, ...patch } });
  };

  // Fall back to internal state if the parent didn't supply one (used in
  // contexts that still embed the preview inline — currently none, but kept
  // for safety so the component stays self-contained).
  const [internalTesting, setInternalTesting] = useState(false);
  const [internalTestResult, setInternalTestResult] = useState<CustomHttpTestResponse | null>(null);
  const testing = externalTesting ?? internalTesting;
  const testResult = externalTestResult ?? internalTestResult;
  const onTest = externalOnTest ?? (async () => {
    setInternalTesting(true);
    try {
      const r = await integrationsApi.testCustomHttp(merged);
      setInternalTestResult(r);
    } finally {
      setInternalTesting(false);
    }
  });

  const showLeft = !placement || placement === 'left';
  const showRight = !placement || placement === 'right';

  return (
    <div className={styles.form}>
      {showLeft && <EndpointSection cfg={merged} update={update} />}
      {showLeft && <AuthSection cfg={merged} update={update} />}
      {showLeft && <PaginationSection cfg={merged} update={update} />}
      {showLeft && merged.method === 'POST' && <BodySection cfg={merged} update={update} />}
      {showRight && <OutputSection cfg={merged} update={update} />}
      {!hideTestSection && !placement && (
        <TestSection cfg={merged} testing={testing} testResult={testResult} onTest={onTest} />
      )}
      {showRight && (
        <RecordsRootSection cfg={merged} update={update} testResult={testResult} />
      )}
      {showRight && !merged.passthrough && (
        <FieldBindingsSection cfg={merged} update={update} testResult={testResult} />
      )}
    </div>
  );
}

// =============================================================================
// Output section — passthrough toggle + discovery depth. Hoisted out of
// FieldBindingsSection so it sits with the rest of the config decisions and
// the user sees it before getting the "at least one binding required"
// validation error.
// =============================================================================

function OutputSection({
  cfg, update,
}: { cfg: CustomHttpConfig; update: (p: Partial<CustomHttpConfig>) => void }) {
  const passthrough = !!cfg.passthrough;
  return (
    <div className={styles.section}>
      <div className={styles.fieldHelp}>
        Choose one of the options below — at least one must be selected.
        <br />
        Use full record emits every JSON key verbatim (including nested ones like{' '}
        <code>info.cpu</code>). Field bindings extract only specific JSONPaths.
      </div>
      <div className={styles.toggleRow}>
        <ToggleSwitch
          checked={passthrough}
          onChange={(checked) => update({ passthrough: checked })}
          label="Use full record (skip bindings)"
        />
      </div>

      {passthrough && (
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor="ch-passthrough-depth">
            Field discovery depth
          </label>
          <input
            id="ch-passthrough-depth"
            type="number"
            min={1}
            max={10}
            className={`${styles.fieldInput} ${styles.fieldInputNoSpinner}`}
            value={cfg.passthrough_depth}
            onChange={(e) => {
              const n = Number.parseInt(e.target.value, 10);
              update({ passthrough_depth: Number.isFinite(n) ? n : 3 });
            }}
          />
          <div className={styles.fieldHelp}>
            How many levels of nested objects to walk when discovering source
            fields from the sample. Default 3. Increase if your records are
            deeply nested.
          </div>
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Endpoint section
// =============================================================================

function EndpointSection({
  cfg, update,
}: { cfg: CustomHttpConfig; update: (p: Partial<CustomHttpConfig>) => void }) {
  return (
    <div className={styles.section}>
      <div className={styles.sectionHeader}>Endpoint</div>

      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="ch-method">
          Method <span className={styles.required} aria-hidden="true">*</span>
        </label>
        <select id="ch-method" className={styles.fieldInput}
          value={cfg.method}
          onChange={(e) => update({ method: e.target.value as CustomHttpConfig['method'] })}>
          <option value="GET">GET</option>
          <option value="POST">POST</option>
        </select>
      </div>

      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="ch-url">
          URL <span className={styles.required} aria-hidden="true">*</span>
        </label>
        <input id="ch-url" type="url" className={styles.fieldInput}
          value={cfg.url}
          onChange={(e) => update({ url: e.target.value })}
          placeholder="https://api.example.com/v1/devices" />
      </div>

      <KeyValueRows
        label="Headers"
        value={cfg.headers}
        onChange={(headers) => update({ headers })}
        keyPlaceholder="Header name"
        valuePlaceholder="Value"
      />

      <KeyValueRows
        label="Static query params"
        value={cfg.static_query_params}
        onChange={(static_query_params) => update({ static_query_params })}
        keyPlaceholder="Param name"
        valuePlaceholder="Value"
      />

      <div className={styles.toggleRow}>
        <ToggleSwitch
          checked={cfg.verify_ssl}
          onChange={(checked) => update({ verify_ssl: checked })}
          label="Verify TLS certificate"
        />
        <div className={styles.fieldHelp}>
          Untick only for trusted internal hosts with self-signed certs —
          disables certificate validation on every request.
        </div>
      </div>
    </div>
  );
}

function KeyValueRows({
  label, value, onChange, keyPlaceholder, valuePlaceholder,
}: {
  label: string;
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
  keyPlaceholder: string;
  valuePlaceholder: string;
}) {
  const entries = Object.entries(value);
  return (
    <div className={styles.field}>
      <div className={styles.fieldLabel}>{label}</div>
      {entries.map(([k, v], i) => (
        <div key={`${k}-${i}`} className={styles.kvRow}>
          <input className={styles.fieldInput} value={k} placeholder={keyPlaceholder}
            onChange={(e) => {
              const next = { ...value };
              delete next[k];
              next[e.target.value] = v;
              onChange(next);
            }} />
          <input className={styles.fieldInput} value={v} placeholder={valuePlaceholder}
            onChange={(e) => onChange({ ...value, [k]: e.target.value })} />
          <button type="button"
            className={styles.removeBtn}
            aria-label={`remove ${label} row ${i}`}
            onClick={() => {
              const next = { ...value };
              delete next[k];
              onChange(next);
            }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
          </button>
        </div>
      ))}
      <Button variant="secondary" size="sm"
        onClick={() => onChange({ ...value, '': '' })}>
        + Add row
      </Button>
    </div>
  );
}

// =============================================================================
// Auth section
// =============================================================================

function AuthSection({
  cfg, update,
}: { cfg: CustomHttpConfig; update: (p: Partial<CustomHttpConfig>) => void }) {
  return (
    <div className={styles.section}>
      <div className={styles.sectionHeader}>Authentication</div>

      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="ch-auth-type">
          Auth type <span className={styles.required} aria-hidden="true">*</span>
        </label>
        <select id="ch-auth-type" className={styles.fieldInput}
          value={cfg.auth_type}
          onChange={(e) => update({ auth_type: e.target.value as CustomHttpConfig['auth_type'] })}>
          <option value="none">None</option>
          <option value="basic">Basic</option>
          <option value="bearer">Bearer Token</option>
          <option value="api_key">API Key</option>
          <option value="oauth2">OAuth2 Client Credentials</option>
          <option value="login_password">Login (JSON username/password → token)</option>
        </select>
      </div>

      {cfg.auth_type === 'basic' && (
        <>
          <TextField id="ch-username" label="Username" required
            value={cfg.username ?? ''} onChange={(v) => update({ username: v })} />
          <PasswordField id="ch-password" label="Password" required
            value={cfg.password ?? ''} onChange={(v) => update({ password: v })} />
        </>
      )}

      {cfg.auth_type === 'bearer' && (
        <PasswordField id="ch-bearer-token" label="Bearer token" required
          value={cfg.bearer_token ?? ''} onChange={(v) => update({ bearer_token: v })} />
      )}

      {cfg.auth_type === 'api_key' && (
        <>
          <PasswordField id="ch-api-key" label="API key" required
            value={cfg.api_key ?? ''} onChange={(v) => update({ api_key: v })} />
          <div className={styles.field}>
            <label className={styles.fieldLabel} htmlFor="ch-api-key-location">
              Location
            </label>
            <select id="ch-api-key-location" className={styles.fieldInput}
              value={cfg.api_key_location}
              onChange={(e) => update({
                api_key_location: e.target.value as 'header' | 'query',
              })}>
              <option value="header">Header</option>
              <option value="query">Query parameter</option>
            </select>
          </div>
          <TextField id="ch-api-key-name" label="Name"
            value={cfg.api_key_name} onChange={(v) => update({ api_key_name: v })} />
          <TextField id="ch-api-key-prefix" label="Prefix"
            value={cfg.api_key_prefix} onChange={(v) => update({ api_key_prefix: v })} />
        </>
      )}

      {cfg.auth_type === 'oauth2' && (
        <>
          <TextField id="ch-token-url" label="Token URL" required
            value={cfg.token_url ?? ''} onChange={(v) => update({ token_url: v })} />
          <TextField id="ch-client-id" label="Client ID" required
            value={cfg.client_id ?? ''} onChange={(v) => update({ client_id: v })} />
          <PasswordField id="ch-client-secret" label="Client secret" required
            value={cfg.client_secret ?? ''} onChange={(v) => update({ client_secret: v })} />
          <TextField id="ch-scope" label="Scope"
            value={cfg.scope ?? ''} onChange={(v) => update({ scope: v })} />
        </>
      )}

      {cfg.auth_type === 'login_password' && (
        <>
          <div className={styles.fieldHelp}>
            For APIs that exchange a JSON-body username/password POST for a
            short-lived token (e.g. PocketBase, Beszel).
          </div>
          <TextField id="ch-login-url" label="Login URL" required
            value={cfg.login_url ?? ''} onChange={(v) => update({ login_url: v })} />
          <TextField id="ch-identity-field" label="Identity field name"
            value={cfg.identity_field ?? 'identity'}
            onChange={(v) => update({ identity_field: v })} />
          <TextField id="ch-identity" label="Identity (username / email)" required
            value={cfg.identity ?? ''} onChange={(v) => update({ identity: v })} />
          <TextField id="ch-password-field" label="Password field name"
            value={cfg.password_field ?? 'password'}
            onChange={(v) => update({ password_field: v })} />
          <PasswordField id="ch-login-password" label="Password" required
            value={cfg.password ?? ''} onChange={(v) => update({ password: v })} />
          <TextField id="ch-token-path" label="Token path (JSONPath)"
            value={cfg.token_path ?? '$.token'}
            onChange={(v) => update({ token_path: v })} />
          <TextField id="ch-token-header" label="Token header"
            value={cfg.token_header ?? 'Authorization'}
            onChange={(v) => update({ token_header: v })} />
          <TextField id="ch-token-prefix" label="Token prefix (e.g. 'Bearer ')"
            value={cfg.token_prefix ?? ''}
            onChange={(v) => update({ token_prefix: v })} />
          <TextField id="ch-token-ttl" label="Token TTL (seconds)"
            value={String(cfg.token_ttl_seconds ?? 900)}
            onChange={(v) => update({ token_ttl_seconds: Number(v) || 900 })} />
        </>
      )}
    </div>
  );
}

function TextField({
  id, label, required, value, onChange,
}: {
  id: string; label: string; required?: boolean;
  value: string; onChange: (v: string) => void;
}) {
  return (
    <div className={styles.field}>
      <label className={styles.fieldLabel} htmlFor={id}>
        {label}{required ? <span className={styles.required} aria-hidden="true"> *</span> : null}
      </label>
      <input id={id} aria-label={label} className={styles.fieldInput} value={value}
        onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function PasswordField({
  id, label, required, value, onChange,
}: {
  id: string; label: string; required?: boolean;
  value: string; onChange: (v: string) => void;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className={styles.field}>
      <label className={styles.fieldLabel} htmlFor={id}>
        {label}{required ? <span className={styles.required} aria-hidden="true"> *</span> : null}
      </label>
      <div className={styles.passwordField}>
        <input id={id} aria-label={label} type={show ? 'text' : 'password'}
          className={styles.fieldInput}
          value={value} onChange={(e) => onChange(e.target.value)} />
        <button type="button" className={styles.passwordToggle}
          aria-pressed={show}
          aria-label={show ? 'Hide value' : 'Show value'}
          onClick={() => setShow((s) => !s)}>
          <EyeIcon open={!show} />
        </button>
      </div>
    </div>
  );
}

// =============================================================================
// Pagination section
// =============================================================================

function PaginationSection({
  cfg, update,
}: { cfg: CustomHttpConfig; update: (p: Partial<CustomHttpConfig>) => void }) {
  return (
    <div className={styles.section}>
      <div className={styles.sectionHeader}>Pagination</div>

      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="ch-pag-type">Pagination type</label>
        <select id="ch-pag-type" className={styles.fieldInput}
          value={cfg.pagination_type}
          onChange={(e) => update({
            pagination_type: e.target.value as CustomHttpConfig['pagination_type'],
          })}>
          <option value="none">None</option>
          <option value="offset">Offset (offset/limit)</option>
          <option value="page">Page-based (page/perPage)</option>
          <option value="cursor">Cursor / next-token</option>
          <option value="link">Link header (RFC 5988)</option>
        </select>
      </div>

      {cfg.pagination_type === 'offset' && (
        <>
          <TextField id="ch-page-size" label="Page size"
            value={String(cfg.page_size)}
            onChange={(v) => update({ page_size: Number(v) || 100 })} />
          <TextField id="ch-offset-param" label="Offset param"
            value={cfg.offset_param}
            onChange={(v) => update({ offset_param: v })} />
          <TextField id="ch-limit-param" label="Limit param"
            value={cfg.limit_param}
            onChange={(v) => update({ limit_param: v })} />
          <TextField id="ch-total-path" label="Total path (optional JSONPath)"
            value={cfg.total_path}
            onChange={(v) => update({ total_path: v })} />
        </>
      )}

      {cfg.pagination_type === 'page' && (
        <>
          <TextField id="ch-page-size-p" label="Page size"
            value={String(cfg.page_size)}
            onChange={(v) => update({ page_size: Number(v) || 100 })} />
          <TextField id="ch-page-param" label="Page param"
            value={cfg.page_param}
            onChange={(v) => update({ page_param: v })} />
          <TextField id="ch-perpage-param" label="Per-page param"
            value={cfg.perpage_param}
            onChange={(v) => update({ perpage_param: v })} />
          <TextField id="ch-start-page" label="Start page (0 or 1)"
            value={String(cfg.start_page)}
            onChange={(v) => update({ start_page: Number(v) || 1 })} />
          <TextField id="ch-total-path-p" label="Total path (optional JSONPath)"
            value={cfg.total_path}
            onChange={(v) => update({ total_path: v })} />
        </>
      )}

      {cfg.pagination_type === 'cursor' && (
        <>
          <TextField id="ch-cursor-req-param" label="Cursor request param"
            value={cfg.cursor_request_param}
            onChange={(v) => update({ cursor_request_param: v })} />
          <TextField id="ch-cursor-resp-path" label="Cursor response path (JSONPath)"
            value={cfg.cursor_response_path}
            onChange={(v) => update({ cursor_response_path: v })} />
        </>
      )}
    </div>
  );
}

// =============================================================================
// Body section (POST only)
// =============================================================================

function BodySection({
  cfg, update,
}: { cfg: CustomHttpConfig; update: (p: Partial<CustomHttpConfig>) => void }) {
  const [text, setText] = useState(
    cfg.static_body ? JSON.stringify(cfg.static_body, null, 2) : '',
  );
  const [error, setError] = useState<string | null>(null);

  // Re-sync the textarea when the saved config changes underneath us
  // (e.g., loading a saved integration in edit mode).
  useEffect(() => {
    setText(cfg.static_body ? JSON.stringify(cfg.static_body, null, 2) : '');
    setError(null);
  }, [cfg.static_body]);

  const validate = () => {
    if (!text.trim()) { setError(null); update({ static_body: null }); return; }
    try {
      const parsed = JSON.parse(text);
      setError(null);
      update({ static_body: parsed });
    } catch {
      setError('Invalid JSON');
    }
  };

  return (
    <div className={styles.section}>
      <div className={styles.sectionHeader}>Request body</div>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="ch-body">Request body (JSON)</label>
        <textarea id="ch-body" aria-label="Request body (JSON)" className={styles.fieldTextarea}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={validate}
          placeholder='{"filter": {"active": true}}' />
        {error && <div className={styles.error}>{error}</div>}
      </div>
    </div>
  );
}

// =============================================================================
// Test section — runs a single HTTP call and shows the raw response so the
// user can read it before picking JSONPaths below.
// =============================================================================

function TestSection({
  cfg, testing, testResult, onTest,
}: {
  cfg: CustomHttpConfig;
  testing: boolean;
  testResult: CustomHttpTestResponse | null;
  onTest: () => void;
}) {
  return (
    <div className={styles.section}>
      <div className={styles.sectionHeader}>Preview response</div>
      <CustomHttpPreviewBody cfg={cfg} testing={testing}
        testResult={testResult} onTest={onTest} />
    </div>
  );
}

/** Standalone body of the preview block (button + status + response pre).
 *  Used both by the inline TestSection above and by ConnectionStep when the
 *  custom_http layout puts the preview on the right column. */
export function CustomHttpPreviewBody({
  cfg, testing, testResult, onTest,
}: {
  cfg: CustomHttpConfig;
  testing: boolean;
  testResult: CustomHttpTestResponse | null;
  onTest: () => void;
}) {
  return (
    <>
      <div className={styles.fieldHelp}>
        Run the request once to see the raw response. You'll use it as a
        reference to pick the records-root path and field bindings on the left.
      </div>
      <div className={styles.previewButtonRow}>
        <Button variant="primary" size="sm"
          onClick={onTest} disabled={testing || !cfg.url}>
          {testing ? 'Loading…' : 'Preview response'}
        </Button>
      </div>

      {testResult && (
        <div role="status" className={styles.testResult}>
          {testResult.success ? (
            <>
              <div className={styles.testStatusOk}>
                Status {testResult.status_code}
                {testResult.elapsed_ms !== undefined && (
                  <span className={styles.testMeta}> · {Math.round(testResult.elapsed_ms)} ms</span>
                )}
              </div>
              {testResult.body_preview !== undefined && (
                <pre className={styles.responseBody}>
                  {formatBodyPreview(testResult.body_preview, testResult.body_truncated)}
                </pre>
              )}
            </>
          ) : (
            <div className={styles.testStatusErr}>
              {testResult.message ?? 'Test failed'}
            </div>
          )}
        </div>
      )}
    </>
  );
}

function formatBodyPreview(body: unknown, truncated?: boolean): string {
  if (typeof body === 'string') return body;
  let json: string;
  try {
    json = JSON.stringify(body, null, 2);
  } catch {
    json = String(body);
  }
  return truncated ? `${json}\n…(truncated)` : json;
}

// =============================================================================
// Records root section
// =============================================================================

function RecordsRootSection({
  cfg, update, testResult,
}: {
  cfg: CustomHttpConfig;
  update: (p: Partial<CustomHttpConfig>) => void;
  testResult: CustomHttpTestResponse | null;
}) {
  const match = testResult?.records_root_match;
  const autoDetected = match?.auto_detected && match.detected_path;
  return (
    <div className={styles.section}>
      <div className={styles.sectionHeader}>Records root</div>
      <TextField id="ch-records-root" label="Records root (JSONPath)"
        value={cfg.records_root}
        onChange={(v) => update({ records_root: v })} />
      <div className={styles.fieldHelp}>
        Leave blank to auto-detect — works for bare arrays and common
        envelopes (<code>$.items</code>, <code>$.data</code>,{' '}
        <code>$.results</code>, …). Override if your API uses a different
        wrapper.
      </div>
      {autoDetected && match?.matched_count !== undefined && (
        <div className={styles.fieldHelp}>
          Auto-detected <code>{match.detected_path}</code> ·{' '}
          {match.matched_count} record(s) matched.{' '}
          <button
            type="button"
            className={styles.linkButton}
            onClick={() => update({ records_root: match.detected_path ?? '' })}
          >
            Pin this path
          </button>
        </div>
      )}
      {match?.valid_path && !autoDetected && match.matched_count !== undefined && (
        <div className={styles.fieldHelp}>
          {match.matched_count} record(s) matched in the last Test response.
        </div>
      )}
      {match && match.valid_path === false && (
        <div className={styles.error}>
          {match.error ?? 'Path did not match the response'}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Field bindings table
// =============================================================================

function suggestSourceName(path: string): string {
  const cleaned = path.replace(/\[[^\]]*\]/g, '');
  const parts = cleaned.split('.').filter((p) => p && p !== '$');
  return parts.join('_');
}

function BindingRow({
  binding,
  index,
  isDuplicate,
  onPathChange,
  onNameChange,
  onRemove,
}: {
  binding: { source_path: string; source_name: string };
  index: number;
  isDuplicate: boolean;
  onPathChange: (i: number, val: string) => void;
  onNameChange: (i: number, val: string) => void;
  onRemove: (i: number) => void;
}) {
  const [localPath, setLocalPath] = useState(binding.source_path);

  return (
    <div className={styles.bindingRow}>
      <input
        className={styles.fieldInput}
        placeholder="JSONPath, e.g. $.hostname"
        value={localPath}
        onChange={(e) => {
          setLocalPath(e.target.value);
          onPathChange(index, e.target.value);
        }}
        onBlur={() => {
          if (!binding.source_name && localPath) {
            onNameChange(index, suggestSourceName(localPath));
          }
        }}
      />
      <input
        className={styles.fieldInput}
        placeholder="source name"
        value={binding.source_name}
        onChange={(e) => onNameChange(index, e.target.value)}
      />
      <button type="button"
        className={styles.removeBtn}
        aria-label={`remove binding ${index}`}
        onClick={() => onRemove(index)}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
      </button>
      {isDuplicate && <div className={styles.error}>duplicate source_name</div>}
    </div>
  );
}

function FieldBindingsSection({
  cfg, update, testResult,
}: {
  cfg: CustomHttpConfig;
  update: (p: Partial<CustomHttpConfig>) => void;
  testResult: CustomHttpTestResponse | null;
}) {
  const bindings = cfg.field_bindings;
  const sampleRecord = testResult?.records_root_match?.sample_records?.[0];

  const counts = bindings.reduce<Record<string, number>>((acc, b) => {
    if (b.source_name) acc[b.source_name] = (acc[b.source_name] ?? 0) + 1;
    return acc;
  }, {});

  const setRow = (
    i: number,
    patch: Partial<{ source_path: string; source_name: string }>,
  ) => {
    const next = bindings.map((b, j) => (j === i ? { ...b, ...patch } : b));
    update({ field_bindings: next });
  };

  return (
    <div className={styles.section}>
      <div className={styles.sectionHeader}>Field bindings</div>
      <div className={styles.fieldHelp}>
        Each binding extracts one value from a record using a JSONPath. You'll map
        these source fields to AssetAtlas target fields in the next step.
      </div>

      {sampleRecord !== undefined && (
        <details className={styles.sampleRecord}>
          <summary>Sample record from the last preview (use as reference)</summary>
          <pre className={styles.responseBody}>
            {JSON.stringify(sampleRecord, null, 2)}
          </pre>
        </details>
      )}

      {bindings.map((b, i) => (
        <BindingRow
          key={i}
          binding={b}
          index={i}
          isDuplicate={!!(b.source_name && counts[b.source_name] > 1)}
          onPathChange={(idx, val) => setRow(idx, { source_path: val })}
          onNameChange={(idx, val) => setRow(idx, { source_name: val })}
          onRemove={(idx) => update({
            field_bindings: bindings.filter((_, j) => j !== idx),
          })}
        />
      ))}

      <Button variant="secondary" size="sm"
        onClick={() => update({
          field_bindings: [...bindings, { source_path: '', source_name: '' }],
        })}>
        + Add binding
      </Button>
    </div>
  );
}
