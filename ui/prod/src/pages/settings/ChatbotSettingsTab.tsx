// SPDX-License-Identifier: AGPL-3.0-or-later
// Admin LLM configuration. One card per provider; each holds its own
// model + api_key (+ provider-specific extras) and per-card Cancel / Test
// / Save. The toggle picks which provider the chatbot uses.

import { useEffect, useRef, useState } from 'react';

import type {
  AdminChatConfig, AdminChatConfigUpdate, AdminChatTestRequest,
  BedrockAuthMode, BedrockModel, ChatProvider,
  FoundryProtocol, ProviderConfig, ProviderConfigUpdate,
} from '@/api/chat';
import {
  getAdminChatConfig, getBedrockModels, testAdminChatConfig,
  updateAdminChatConfig,
} from '@/api/chat';
import { Button } from '@/components/ui/Button';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import { InlineStatus, successFor, useInlineStatus } from '@/components/ui/InlineStatus';
import type { InlineStatusValue } from '@/components/ui/InlineStatus';
import { Modal } from '@/components/ui/Modal';
import { EyeIcon } from '@/components/ui/EyeIcon';
import { assetUrl } from '@/basePath';
import styles from './ChatbotSettingsTab.module.css';

interface ProviderCardSpec {
  value: ChatProvider;
  label: string;
  subtitle: string;
  logo?: string;
  /** Single-colour black glyph that must invert to white in dark mode. */
  monochromeLogo?: boolean;
}

const PROVIDERS: ProviderCardSpec[] = [
  {
    value: 'anthropic',
    label: 'Anthropic',
    subtitle: 'Hosted Claude models.',
    logo: '/logos/Claude.png',
  },
  {
    value: 'openai',
    label: 'OpenAI',
    subtitle: 'Hosted gpt-4o / gpt-4.1 models.',
    logo: '/logos/ChatGPT.png',
    monochromeLogo: true,
  },
  {
    value: 'aws_bedrock',
    label: 'AWS Bedrock',
    subtitle: 'Hosted Anthropic, Llama, Mistral, Cohere via Amazon Bedrock.',
    logo: '/logos/aws-bedrock.png',
  },
  {
    value: 'openai_compat',
    label: 'OpenAI-compatible',
    subtitle: 'Self-hosted vLLM, Ollama, LiteLLM. Requires a Base URL.',
  },
  {
    value: 'foundry',
    label: 'Microsoft Foundry',
    subtitle: 'Azure-hosted Anthropic, OpenAI Chat, or OpenAI Responses via Foundry.',
    logo: '/logos/ms_foundry.png',
  },
];

type ApiKeyDraft =
  | { mode: 'unchanged' }
  | { mode: 'new'; value: string };

interface ProviderDraft {
  model: string;
  api_key: ApiKeyDraft;
  base_url: string;
  prompt_cache: boolean;
  thinking_enabled: boolean;
  // Bedrock-only.
  aws_region: string;
  // null = never chosen/​saved. The form renders a default ('access_keys'),
  // but keeping the raw value lets the dirty-diff detect an explicit choice
  // that happens to equal the default (see #910).
  auth_mode: BedrockAuthMode | null;
  access_key_id: ApiKeyDraft;
  secret_access_key: ApiKeyDraft;
  role_arn: string;
  external_id: string;
  base_access_key_id: ApiKeyDraft;
  base_secret_access_key: ApiKeyDraft;
  // Foundry-only.
  foundry_protocol: FoundryProtocol | null;
}

interface PageDraft {
  active: ChatProvider | null;
  providers: Record<ChatProvider, ProviderDraft>;
}

const EMPTY_PROVIDER_DRAFT: ProviderDraft = {
  model: '',
  api_key: { mode: 'unchanged' },
  base_url: '',
  prompt_cache: true,
  thinking_enabled: false,
  aws_region: '',
  auth_mode: null,
  access_key_id: { mode: 'unchanged' },
  secret_access_key: { mode: 'unchanged' },
  role_arn: '',
  external_id: '',
  base_access_key_id: { mode: 'unchanged' },
  base_secret_access_key: { mode: 'unchanged' },
  foundry_protocol: null,
};

function draftFromProvider(p: ProviderConfig): ProviderDraft {
  return {
    ...EMPTY_PROVIDER_DRAFT,
    model: p.model,
    base_url: p.base_url,
    prompt_cache: p.prompt_cache,
    thinking_enabled: p.thinking_enabled,
    aws_region: p.aws_region,
    // Keep the raw stored value (may be null). The form substitutes a display
    // default at render time; the dirty-diff compares against this raw value
    // so an explicit choice equal to the default still registers (#910).
    auth_mode: p.auth_mode,
    role_arn: p.role_arn,
    external_id: p.external_id,
    foundry_protocol: p.foundry_protocol,
  };
}

function pageDraftFromConfig(c: AdminChatConfig): PageDraft {
  return {
    active: c.active_provider,
    providers: {
      anthropic: draftFromProvider(c.providers.anthropic),
      openai: draftFromProvider(c.providers.openai),
      openai_compat: draftFromProvider(c.providers.openai_compat),
      aws_bedrock: draftFromProvider(c.providers.aws_bedrock),
      foundry: draftFromProvider(c.providers.foundry),
    },
  };
}

function placeholderModelFor(provider: ChatProvider): string {
  switch (provider) {
    case 'anthropic': return 'claude-sonnet-4-6';
    case 'openai': return 'gpt-4o-mini';
    case 'openai_compat': return 'llama-3.1-8b-instruct';
    case 'aws_bedrock': return 'anthropic.claude-3-5-sonnet-20241022-v2:0';
    case 'foundry': return 'claude-sonnet-4-6';
  }
}

function errorMessage(err: unknown): string {
  if (err && typeof err === 'object' && 'message' in err) {
    return String((err as { message: unknown }).message);
  }
  return String(err);
}

function loadedFor(p: ChatProvider, config: AdminChatConfig): ProviderDraft {
  return draftFromProvider(config.providers[p]);
}

function isCardDirty(card: ProviderDraft, loaded: ProviderDraft): boolean {
  return (
    card.model !== loaded.model
    || card.base_url !== loaded.base_url
    || card.prompt_cache !== loaded.prompt_cache
    || card.thinking_enabled !== loaded.thinking_enabled
    || card.aws_region !== loaded.aws_region
    || card.auth_mode !== loaded.auth_mode
    || card.role_arn !== loaded.role_arn
    || card.external_id !== loaded.external_id
    || card.foundry_protocol !== loaded.foundry_protocol
    || card.api_key.mode !== 'unchanged'
    || card.access_key_id.mode !== 'unchanged'
    || card.secret_access_key.mode !== 'unchanged'
    || card.base_access_key_id.mode !== 'unchanged'
    || card.base_secret_access_key.mode !== 'unchanged'
  );
}

// A secret field counts as "filled" when the user typed a new value OR an
// existing value is already stored and left unchanged.
function apiKeyFilled(key: ApiKeyDraft, storedConfigured: boolean): boolean {
  return key.mode === 'new' ? key.value.trim().length > 0 : storedConfigured;
}

// Whether the provider's required fields are all present — gates the Test
// button (#911). Required sets differ per provider; see each case.
function requiredFieldsFilled(
  p: ChatProvider, card: ProviderDraft, saved: ProviderConfig,
): boolean {
  const model = card.model.trim() !== '';
  const key = apiKeyFilled(card.api_key, saved.api_key_configured);
  switch (p) {
    case 'anthropic':
    case 'openai':
      return model && key;
    case 'openai_compat':
      return model && key && card.base_url.trim() !== '';
    case 'foundry':
      // Protocol always has a rendered default, so it's never "missing".
      return model && key && card.base_url.trim() !== '';
    case 'aws_bedrock': {
      if (card.aws_region.trim() === '') return false;
      const mode = card.auth_mode ?? 'access_keys';
      if (mode === 'assume_role') return card.role_arn.trim() !== '';
      return (
        apiKeyFilled(card.access_key_id, Boolean(saved.access_key_id_preview))
        && apiKeyFilled(
          card.secret_access_key,
          saved.api_key_configured && saved.auth_mode === 'access_keys',
        )
      );
    }
  }
}

export function ChatbotSettingsTab() {
  const [status, setStatus] = useInlineStatus();
  const [config, setConfig] = useState<AdminChatConfig | null>(null);
  const [draft, setDraft] = useState<PageDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [savingProvider, setSavingProvider] = useState<ChatProvider | null>(null);
  const [testingProvider, setTestingProvider] = useState<ChatProvider | null>(null);
  // Which provider tested OK this session — gates enabling that provider
  // (you must save + test before it can be turned on). Cleared on edit/save.
  const [testOkProvider, setTestOkProvider] = useState<ChatProvider | null>(null);
  // A successful save/test turns the clicked card's button green (the status
  // action encodes the provider, so only that card flashes); any operation
  // failure (test, save, switch) shows the raw error in a modal — never a
  // top-page banner.
  const [errorModal, setErrorModal] = useState<{ title: string; message: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAdminChatConfig()
      .then((c) => {
        if (cancelled) return;
        setConfig(c);
        setDraft(pageDraftFromConfig(c));
      })
      .catch((err) => setStatus({ kind: 'error', message: errorMessage(err) }))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [setStatus]);

  if (loading || !draft || !config) return <LoadingSpinner />;

  function patchBodyFor(p: ChatProvider): AdminChatConfigUpdate {
    if (!config) return {};
    const card = draft!.providers[p];
    const loaded = loadedFor(p, config);
    const providerPatch: ProviderConfigUpdate = {};

    if (card.model !== loaded.model) providerPatch.model = card.model;

    if (p === 'aws_bedrock') {
      if (card.aws_region !== loaded.aws_region) {
        providerPatch.aws_region = card.aws_region;
      }
      if (card.auth_mode && card.auth_mode !== loaded.auth_mode) {
        providerPatch.auth_mode = card.auth_mode;
      }
      if (card.access_key_id.mode === 'new') {
        providerPatch.access_key_id = card.access_key_id.value;
      }
      if (card.secret_access_key.mode === 'new') {
        providerPatch.secret_access_key = card.secret_access_key.value;
      }
      if (card.role_arn !== loaded.role_arn) {
        providerPatch.role_arn = card.role_arn;
      }
      if (card.external_id !== loaded.external_id) {
        providerPatch.external_id = card.external_id;
      }
      if (card.base_access_key_id.mode === 'new') {
        providerPatch.base_access_key_id = card.base_access_key_id.value;
      }
      if (card.base_secret_access_key.mode === 'new') {
        providerPatch.base_secret_access_key = card.base_secret_access_key.value;
      }
      // Compose rule: when any auth field is being sent, the backend needs
      // auth_mode to assemble the JSON blob. If the user never touched the
      // radio (raw value still null) fall back to the rendered default.
      const sendingAuthField =
        providerPatch.access_key_id !== undefined
        || providerPatch.secret_access_key !== undefined
        || providerPatch.role_arn !== undefined
        || providerPatch.external_id !== undefined
        || providerPatch.base_access_key_id !== undefined
        || providerPatch.base_secret_access_key !== undefined;
      if (sendingAuthField && providerPatch.auth_mode === undefined) {
        providerPatch.auth_mode = card.auth_mode ?? 'access_keys';
      }
    } else {
      if (card.base_url !== loaded.base_url) providerPatch.base_url = card.base_url;
      if (card.prompt_cache !== loaded.prompt_cache) providerPatch.prompt_cache = card.prompt_cache;
      if (card.thinking_enabled !== loaded.thinking_enabled) providerPatch.thinking_enabled = card.thinking_enabled;
      if (card.api_key.mode === 'new') providerPatch.api_key = card.api_key.value;
      if (p === 'foundry') {
        // A protocol the user actually picked (raw, non-null) that differs
        // from the stored value is dirty and sent as-is. Compose rule: when
        // other foundry fields are being sent on a row whose protocol was
        // never stored, include the rendered default so the backend persists
        // a complete config.
        const sendingFoundryField =
          providerPatch.model !== undefined
          || providerPatch.base_url !== undefined
          || providerPatch.api_key !== undefined;
        const savedProtocol = config!.providers[p].foundry_protocol;
        if (card.foundry_protocol && card.foundry_protocol !== loaded.foundry_protocol) {
          providerPatch.foundry_protocol = card.foundry_protocol;
        } else if (sendingFoundryField && !savedProtocol) {
          providerPatch.foundry_protocol = card.foundry_protocol ?? 'anthropic';
        }
      }
    }

    const body: AdminChatConfigUpdate = {};
    if (Object.keys(providerPatch).length > 0) {
      body.providers = { [p]: providerPatch };
    }
    if (draft!.active === p && config.active_provider !== p) {
      body.active_provider = p;
    }
    return body;
  }

  function testRequestFor(p: ChatProvider): AdminChatTestRequest {
    const card = draft!.providers[p];
    const req: AdminChatTestRequest = {
      provider: p,
      model: card.model,
      base_url: card.base_url,
      prompt_cache: card.prompt_cache,
      thinking_enabled: card.thinking_enabled,
    };
    if (p === 'aws_bedrock') {
      req.aws_region = card.aws_region;
      req.auth_mode = card.auth_mode ?? 'access_keys';
      if (card.access_key_id.mode === 'new') req.access_key_id = card.access_key_id.value;
      if (card.secret_access_key.mode === 'new') req.secret_access_key = card.secret_access_key.value;
      req.role_arn = card.role_arn;
      req.external_id = card.external_id;
      if (card.base_access_key_id.mode === 'new') req.base_access_key_id = card.base_access_key_id.value;
      if (card.base_secret_access_key.mode === 'new') req.base_secret_access_key = card.base_secret_access_key.value;
    } else if (card.api_key.mode === 'new') {
      req.api_key = card.api_key.value;
    }
    if (p === 'foundry') {
      req.foundry_protocol = card.foundry_protocol ?? 'anthropic';
    }
    return req;
  }

  async function handleSaveCard(p: ChatProvider) {
    if (!draft || !config) return;
    const body = patchBodyFor(p);
    if (!body.providers && body.active_provider === undefined) return;

    setSavingProvider(p);
    try {
      const updated = await updateAdminChatConfig(body);
      setConfig(updated);
      // Reset ONLY the saved card's draft to the freshly stored values;
      // other cards keep any in-progress edits (saves are per-provider now).
      setDraft((d) => (d ? {
        active: updated.active_provider,
        providers: { ...d.providers, [p]: draftFromProvider(updated.providers[p]) },
      } : d));
      setTestOkProvider(null);
      setStatus({ kind: 'success', message: 'Chatbot settings saved', action: `save:${p}` });
    } catch (err) {
      // Raw error, in a modal — not the top-page banner.
      setErrorModal({ title: 'Save failed', message: errorMessage(err) });
    } finally {
      setSavingProvider(null);
    }
  }

  async function handleTestCard(p: ChatProvider) {
    setTestingProvider(p);
    setTestOkProvider(null);
    setErrorModal(null);
    try {
      const result = await testAdminChatConfig(testRequestFor(p));
      if (result.ok) {
        // Success gates enabling the provider (#912) and turns the card's
        // Test button green.
        setTestOkProvider(p);
        setStatus({ kind: 'success', message: 'Connection successful', action: `test:${p}` });
      } else {
        // Keep the provider's raw detail verbatim.
        setErrorModal({
          title: 'Connection test failed',
          message: result.detail || 'The connection test failed.',
        });
      }
    } catch (err) {
      setErrorModal({
        title: 'Connection test failed',
        message: errorMessage(err),
      });
    } finally {
      setTestingProvider(null);
    }
  }

  function handleCancelCard(p: ChatProvider) {
    if (!config) return;
    setTestOkProvider((prev) => (prev === p ? null : prev));
    setDraft((d) => {
      if (!d) return d;
      return {
        ...d,
        providers: {
          ...d.providers,
          [p]: loadedFor(p, config),
        },
      };
    });
  }

  function setProviderDraft(p: ChatProvider, patch: Partial<ProviderDraft>) {
    // Editing a card invalidates its last "Test successful" result.
    setTestOkProvider((prev) => (prev === p ? null : prev));
    setDraft((d) => {
      if (!d) return d;
      return {
        ...d,
        providers: {
          ...d.providers,
          [p]: { ...d.providers[p], ...patch },
        },
      };
    });
  }

  // Switch the active provider, or pass null to disable every provider (#912).
  async function setActiveProvider(next: ChatProvider | null) {
    if (!draft || !config) return;
    if (config.active_provider === next) return;
    // Optimistic flip so the toggle responds immediately. We only patch
    // active_provider here — per-provider draft state is intentionally
    // preserved so a switch doesn't wipe an in-progress edit on another card.
    const prevActive = config.active_provider;
    setDraft((d) => (d ? { ...d, active: next } : d));
    try {
      const updated = await updateAdminChatConfig({ active_provider: next });
      setConfig(updated);
      // No success banner — the toggle state is its own feedback (#910 UX).
    } catch (err) {
      setDraft((d) => (d ? { ...d, active: prevActive } : d));
      const verb = next === null ? 'Disable' : 'Switch';
      setErrorModal({ title: `${verb} failed`, message: errorMessage(err) });
    }
  }

  return (
    <div className={styles.page}>
      <InlineStatus status={status} />
      {!config.chat_enabled && (
        <div className={styles.warningBanner}>
          <strong>Chatbot is disabled.</strong> Set <code>CHAT_ENABLED=true</code>
          {' '}in <code>.env</code> and restart the API. Changes here will apply
          once the chat blueprint is active.
        </div>
      )}
      {config.chat_enabled && draft.active === null && (
        <div className={styles.noProviderNotice}>
          <strong>No provider enabled.</strong> The chatbot is turned off.
          {' '}Enable a provider below to start chatting.
        </div>
      )}

      <div className={styles.providerGrid}>
        {PROVIDERS.map((p) => {
          const cardDraft = draft.providers[p.value];
          const loaded = loadedFor(p.value, config);
          const cardDirty = isCardDirty(cardDraft, loaded);
          const isActive = draft.active === p.value;
          const savedProvider = config.providers[p.value];
          return (
            <ProviderCard
              key={p.value}
              spec={p}
              active={isActive}
              onToggleActive={(v) => setActiveProvider(v ? p.value : null)}
              providerDraft={cardDraft}
              setProviderDraft={(patch) => setProviderDraft(p.value, patch)}
              savedProvider={savedProvider}
              dirty={cardDirty}
              saving={savingProvider === p.value}
              testing={testingProvider === p.value}
              testOk={testOkProvider === p.value}
              busy={savingProvider === p.value || testingProvider === p.value}
              onSave={() => handleSaveCard(p.value)}
              onTest={() => handleTestCard(p.value)}
              onCancel={() => handleCancelCard(p.value)}
              saveSuccess={successFor(status, `save:${p.value}`)}
              testSuccess={successFor(status, `test:${p.value}`)}
            />
          );
        })}
      </div>

      {/* Any operation error (test / save / switch) — raw message, in a modal,
          never a top-page banner. */}
      <Modal
        isOpen={errorModal !== null}
        onClose={() => setErrorModal(null)}
        title={errorModal?.title ?? ''}
        size="sm"
      >
        <p className={styles.testErrorText}>{errorModal?.message}</p>
        <div className={styles.testErrorActions}>
          <Button variant="primary" size="sm" onClick={() => setErrorModal(null)}>
            Close
          </Button>
        </div>
      </Modal>
    </div>
  );
}

interface ProviderCardProps {
  spec: ProviderCardSpec;
  active: boolean;
  onToggleActive: (next: boolean) => void;
  providerDraft: ProviderDraft;
  setProviderDraft: (patch: Partial<ProviderDraft>) => void;
  savedProvider: ProviderConfig;
  dirty: boolean;
  saving: boolean;
  testing: boolean;
  testOk: boolean;
  busy: boolean;
  onSave: () => void;
  onTest: () => void;
  onCancel: () => void;
  saveSuccess: InlineStatusValue | null;
  testSuccess: InlineStatusValue | null;
}

function ProviderCard({
  spec, active, onToggleActive, providerDraft, setProviderDraft,
  savedProvider, dirty, saving, testing, testOk, busy,
  onSave, onTest, onCancel, saveSuccess, testSuccess,
}: ProviderCardProps) {
  const cls = `${styles.providerCard} ${active ? styles.providerCardActive : ''}`;
  const isBedrock = spec.value === 'aws_bedrock';
  const canTest = requiredFieldsFilled(spec.value, providerDraft, savedProvider);
  // #912: an inactive provider can only be enabled once it's saved (not dirty)
  // and has passed a Test this session. The active card can always be toggled
  // off (which disables chat entirely).
  const canEnable = !dirty && testOk;

  return (
    <div className={cls}>
      <div className={styles.cardHeader}>
        {spec.logo && (
          <img
            className={`${styles.cardLogo} ${isBedrock ? styles.cardLogoBedrock : ''} ${spec.monochromeLogo ? styles.cardLogoMono : ''}`}
            src={assetUrl(spec.logo)}
            alt={`${spec.label} logo`}
          />
        )}
        <div className={styles.cardTitleBlock}>
          <div className={styles.cardTitle}>{spec.label}</div>
          <div className={styles.cardSubtitle}>{spec.subtitle}</div>
        </div>
        <ToggleSwitch
          checked={active}
          disabled={busy || (!active && !canEnable)}
          onChange={onToggleActive}
        />
      </div>

      <div className={styles.cardBody}>
        {isBedrock ? (
          <BedrockFields
            providerDraft={providerDraft}
            setProviderDraft={setProviderDraft}
            savedProvider={savedProvider}
          />
        ) : (
          <>
            <Field label="Model">
              <input
                type="text"
                className={styles.textInput}
                value={providerDraft.model}
                placeholder={placeholderModelFor(spec.value)}
                onChange={(e) => setProviderDraft({ model: e.target.value })}
              />
            </Field>

            <Field label="API key">
              <ApiKeyControl
                apiKeyDraft={providerDraft.api_key}
                apiKeyConfigured={savedProvider.api_key_configured}
                apiKeyPreview={savedProvider.api_key_preview}
                onChange={(api_key) => setProviderDraft({ api_key })}
                editPlaceholder={savedProvider.api_key_configured ? 'Enter new API key' : 'Paste API key'}
              />
            </Field>

            {spec.value === 'openai_compat' && (
              <>
                <Field label="Base URL">
                  <input
                    type="text"
                    className={styles.textInput}
                    value={providerDraft.base_url}
                    placeholder="Full URL including /v1"
                    onChange={(e) => setProviderDraft({ base_url: e.target.value })}
                  />
                </Field>
                <Field
                  label="Reasoning"
                  hint={<>Chain-of-thought pass via <code>chat_template_kwargs.enable_thinking</code>. Off for faster simple queries.</>}
                >
                  <ToggleSwitch
                    checked={providerDraft.thinking_enabled}
                    onChange={(v) => setProviderDraft({ thinking_enabled: v })}
                  />
                </Field>
              </>
            )}

            {spec.value === 'foundry' && (
              <>
                <Field
                  label="Protocol"
                  hint={<>Which Foundry protocol the model speaks. Determines which client the chatbot uses.</>}
                >
                  <select
                    className={styles.textInput}
                    value={providerDraft.foundry_protocol ?? 'anthropic'}
                    onChange={(e) => setProviderDraft({ foundry_protocol: e.target.value as FoundryProtocol })}
                  >
                    <option value="anthropic">Anthropic Messages</option>
                    <option value="openai_chat">OpenAI Chat Completions</option>
                    <option value="openai_responses">OpenAI Responses</option>
                  </select>
                </Field>
                <Field
                  label="Target URL"
                  hint={<>Paste the full URL from Microsoft Foundry, including any <code>?api-version=…</code>.</>}
                >
                  <input
                    type="text"
                    className={styles.textInput}
                    value={providerDraft.base_url}
                    placeholder="https://…/anthropic/v1/messages"
                    onChange={(e) => setProviderDraft({ base_url: e.target.value })}
                  />
                </Field>
              </>
            )}

            {spec.value === 'anthropic' && (
              <Field
                label="Prompt caching"
                hint={<>Caches the system prompt and last tool spec for cheaper repeat calls.</>}
              >
                <ToggleSwitch
                  checked={providerDraft.prompt_cache}
                  onChange={(v) => setProviderDraft({ prompt_cache: v })}
                />
              </Field>
            )}
          </>
        )}
      </div>

      <div className={styles.cardFooter}>
        <Button
          size="sm"
          variant="secondary"
          onClick={onCancel}
          disabled={!dirty || busy}
        >
          Cancel
        </Button>
        <div className={styles.cardFooterRight}>
          <Button
            size="sm"
            variant="secondary"
            onClick={onTest}
            disabled={busy || !canTest}
            title={!canTest ? 'Fill in the required fields to test the connection' : undefined}
            success={testSuccess}
          >
            {testing ? 'Testing…' : 'Test connection'}
          </Button>
          <Button
            size="sm"
            variant="primary"
            onClick={onSave}
            disabled={!dirty || busy}
            success={saveSuccess}
            successLabel="Saved"
          >
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </div>
    </div>
  );
}

interface BedrockFieldsProps {
  providerDraft: ProviderDraft;
  setProviderDraft: (patch: Partial<ProviderDraft>) => void;
  savedProvider: ProviderConfig;
}

function BedrockFields({
  providerDraft, setProviderDraft, savedProvider,
}: BedrockFieldsProps) {
  const [models, setModels] = useState<BedrockModel[] | null>(null);
  const [modelsSource, setModelsSource] = useState<'live' | 'fallback' | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const refreshReqIdRef = useRef(0);

  async function refreshModels() {
    if (!providerDraft.aws_region) return;
    const myReq = ++refreshReqIdRef.current;
    setRefreshing(true);
    try {
      const resp = await getBedrockModels(providerDraft.aws_region);
      if (refreshReqIdRef.current !== myReq) return;  // stale response
      setModels(resp.models);
      setModelsSource(resp.source);
    } catch {
      if (refreshReqIdRef.current !== myReq) return;
      setModels([]);
      setModelsSource('fallback');
    } finally {
      if (refreshReqIdRef.current === myReq) setRefreshing(false);
    }
  }

  useEffect(() => {
    // Re-fetch whenever the saved credentials change (region edit + Save,
    // access keys re-saved, auth-mode switch). access_key_id_preview is a
    // stable proxy for "credentials were just persisted" without triggering
    // on every keystroke in the form.
    if (savedProvider.aws_region) void refreshModels();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    savedProvider.aws_region,
    savedProvider.access_key_id_preview,
    savedProvider.auth_mode,
    savedProvider.role_arn,
  ]);

  // The stored value may be null (never chosen); render the default here
  // while keeping the raw value in state for dirty-detection.
  const authMode = providerDraft.auth_mode ?? 'access_keys';

  return (
    <>
      <Field label="Authentication">
        <div className={styles.radioGroup}>
          <label className={styles.radioOption}>
            <input
              type="radio"
              name="auth_mode"
              value="access_keys"
              checked={authMode === 'access_keys'}
              onChange={() => setProviderDraft({ auth_mode: 'access_keys' })}
            />
            Access keys
          </label>
          <label className={styles.radioOption}>
            <input
              type="radio"
              name="auth_mode"
              value="assume_role"
              checked={authMode === 'assume_role'}
              onChange={() => setProviderDraft({ auth_mode: 'assume_role' })}
            />
            Assume role
          </label>
        </div>
      </Field>

      <Field label="AWS Region">
        <input
          type="text"
          className={styles.textInput}
          value={providerDraft.aws_region}
          placeholder="us-east-1"
          onChange={(e) => setProviderDraft({ aws_region: e.target.value })}
        />
      </Field>

      {authMode === 'access_keys' ? (
        <>
          <Field label="Access Key ID">
            <ApiKeyControl
              apiKeyDraft={providerDraft.access_key_id}
              apiKeyConfigured={Boolean(savedProvider.access_key_id_preview)}
              apiKeyPreview={savedProvider.access_key_id_preview}
              onChange={(v) => setProviderDraft({ access_key_id: v })}
              editPlaceholder="AKIA..."
            />
          </Field>
          <Field label="Secret Access Key">
            <ApiKeyControl
              apiKeyDraft={providerDraft.secret_access_key}
              apiKeyConfigured={savedProvider.api_key_configured && savedProvider.auth_mode === 'access_keys'}
              apiKeyPreview={savedProvider.api_key_configured ? '••••••••' : ''}
              onChange={(v) => setProviderDraft({ secret_access_key: v })}
              editPlaceholder="Paste secret access key"
            />
          </Field>
        </>
      ) : (
        <>
          <Field label="IAM Role ARN">
            <input
              type="text"
              className={styles.textInput}
              value={providerDraft.role_arn}
              placeholder="arn:aws:iam::123456789012:role/BedrockAccess"
              onChange={(e) => setProviderDraft({ role_arn: e.target.value })}
            />
          </Field>
          <Field
            label="External ID (optional)"
            hint={<>Required when the role's trust policy enforces an <code>sts:ExternalId</code> condition. Leave blank otherwise.</>}
          >
            <input
              type="text"
              className={styles.textInput}
              value={providerDraft.external_id}
              placeholder="e.g. assetatlas-prod"
              onChange={(e) => setProviderDraft({ external_id: e.target.value })}
            />
          </Field>
          <Field
            label="Base Access Key ID (optional)"
            hint={<>The IAM user keys used to call <code>sts:AssumeRole</code>. Leave blank to use the server's ambient AWS identity (env vars / instance role).</>}
          >
            <ApiKeyControl
              apiKeyDraft={providerDraft.base_access_key_id}
              apiKeyConfigured={Boolean(savedProvider.base_access_key_id_preview)}
              apiKeyPreview={savedProvider.base_access_key_id_preview}
              onChange={(v) => setProviderDraft({ base_access_key_id: v })}
              editPlaceholder="AKIA..."
            />
          </Field>
          <Field label="Base Secret Access Key (optional)">
            <ApiKeyControl
              apiKeyDraft={providerDraft.base_secret_access_key}
              apiKeyConfigured={Boolean(savedProvider.base_access_key_id_preview)}
              apiKeyPreview={savedProvider.base_access_key_id_preview ? '••••••••' : ''}
              onChange={(v) => setProviderDraft({ base_secret_access_key: v })}
              editPlaceholder="Paste base secret access key"
            />
          </Field>
        </>
      )}

      <Field label="Model">
        <div className={styles.modelSelectRow}>
          <select
            className={styles.textInput}
            value={providerDraft.model}
            onChange={(e) => setProviderDraft({ model: e.target.value })}
          >
            <option value="">Select a model</option>
            {(models ?? []).map((m) => (
              <option key={m.id} value={m.id}>
                {m.name} ({m.id})
              </option>
            ))}
            {/* Surface a saved-but-no-longer-listed model so the user sees
                what's persisted (e.g. a legacy model filtered by the API).
                Without this the select silently displays "Select a model"
                while still POSTing the stale id. */}
            {providerDraft.model
              && !(models ?? []).some((m) => m.id === providerDraft.model) && (
              <option key={providerDraft.model} value={providerDraft.model}>
                {providerDraft.model} (not in current list)
              </option>
            )}
          </select>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => void refreshModels()}
            disabled={refreshing || !providerDraft.aws_region}
          >
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </Button>
        </div>
        {modelsSource === 'fallback' && (
          <div className={styles.fieldHint}>
            Showing curated list — couldn’t reach AWS with the saved credentials.
          </div>
        )}
      </Field>
    </>
  );
}

function Field({
  label, hint, children,
}: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className={styles.field}>
      <label className={styles.fieldLabel}>{label}</label>
      {children}
      {hint && <div className={styles.fieldHint}>{hint}</div>}
    </div>
  );
}

interface ApiKeyControlProps {
  apiKeyDraft: ApiKeyDraft;
  apiKeyConfigured: boolean;
  apiKeyPreview: string | null;
  onChange: (next: ApiKeyDraft) => void;
  editPlaceholder?: string;
}

function ApiKeyControl({
  apiKeyDraft, apiKeyConfigured, apiKeyPreview, onChange,
  editPlaceholder,
}: ApiKeyControlProps) {
  const [reveal, setReveal] = useState(false);
  const editing = apiKeyDraft.mode === 'new';

  if (editing) {
    return (
      <div className={styles.keyRow}>
        <div className={styles.keyInputWrap}>
          <input
            type={reveal ? 'text' : 'password'}
            className={`${styles.textInput} ${styles.textInputWithEye}`}
            value={apiKeyDraft.value}
            placeholder={editPlaceholder ?? (apiKeyConfigured ? 'Enter new value' : 'Paste value')}
            autoFocus
            onChange={(e) => onChange({ mode: 'new', value: e.target.value })}
          />
          <button
            type="button"
            className={styles.keyEyeBtn}
            title={reveal ? 'Hide' : 'Show'}
            aria-label={reveal ? 'Hide' : 'Show'}
            onClick={() => setReveal((v) => !v)}
          >
            <EyeIcon size={16} open={!reveal} />
          </button>
        </div>
        <IconButton
          title="Cancel"
          onClick={() => {
            setReveal(false);
            onChange({ mode: 'unchanged' });
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </IconButton>
      </div>
    );
  }

  return (
    <div className={styles.keyRow}>
      <div className={styles.keyPreview}>
        {apiKeyConfigured && apiKeyPreview ? (
          <code>{apiKeyPreview}</code>
        ) : (
          <span className={styles.keyPreviewEmpty}>Not configured</span>
        )}
      </div>
      <IconButton
        title="Edit"
        onClick={() => onChange({ mode: 'new', value: '' })}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
        </svg>
      </IconButton>
    </div>
  );
}

function IconButton({
  title, onClick, children,
}: {
  title: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      className={styles.iconBtn}
      title={title}
      aria-label={title}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
