// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Card-state correctness for ChatbotSettingsTab (issues #910 + #911):
 *  - #910a: acting on one provider card must not disable/​wipe the others.
 *  - #910b: a radio/​select change alone (auth_mode / foundry_protocol) on a
 *           never-saved row must register as dirty and be sent in the PATCH.
 *  - #911 : Test connection is disabled until the provider's required fields
 *           are filled (a stored, unchanged key counts as filled).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  render, screen, waitFor, within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ChatbotSettingsTab } from '../ChatbotSettingsTab';

vi.mock('@/api/chat', () => ({
  getAdminChatConfig: vi.fn(),
  updateAdminChatConfig: vi.fn(),
  testAdminChatConfig: vi.fn(),
  getBedrockModels: vi.fn(),
}));

import * as chatApi from '@/api/chat';

const EMPTY_PROVIDER = {
  api_key_preview: '',
  api_key_configured: false,
  base_url: '',
  prompt_cache: true,
  thinking_enabled: false,
  aws_region: '',
  auth_mode: null,
  access_key_id_preview: '',
  role_arn: '',
  external_id: '',
  base_access_key_id_preview: '',
  foundry_protocol: null,
} as const;

/** A config where anthropic + openai are both fully configured (model + key). */
function makeConfig() {
  return {
    active_provider: 'anthropic' as const,
    chat_enabled: true,
    providers: {
      anthropic: {
        ...EMPTY_PROVIDER, provider: 'anthropic' as const,
        model: 'claude-sonnet-4-6', api_key_preview: '••••abcd', api_key_configured: true,
      },
      openai: {
        ...EMPTY_PROVIDER, provider: 'openai' as const,
        model: 'gpt-4o-mini', api_key_preview: '••••wxyz', api_key_configured: true,
      },
      openai_compat: { ...EMPTY_PROVIDER, provider: 'openai_compat' as const, model: '' },
      aws_bedrock: { ...EMPTY_PROVIDER, provider: 'aws_bedrock' as const, model: '' },
      foundry: { ...EMPTY_PROVIDER, provider: 'foundry' as const, model: '' },
    },
  };
}

async function cardFor(label: string) {
  const styles = (await import('../ChatbotSettingsTab.module.css')).default;
  const heading = screen.getByText(label, { exact: true });
  const el = heading.closest(`.${styles.providerCard}`) as HTMLElement;
  expect(el).not.toBeNull();
  return within(el);
}

beforeEach(() => {
  vi.mocked(chatApi.getAdminChatConfig).mockResolvedValue(makeConfig());
  vi.mocked(chatApi.getBedrockModels).mockResolvedValue({ models: [], source: 'fallback' });
  vi.mocked(chatApi.updateAdminChatConfig).mockResolvedValue(makeConfig());
  vi.mocked(chatApi.testAdminChatConfig).mockResolvedValue({
    ok: true, detail: 'ok', sample_text: 'OK', provider: 'anthropic', model: 'x',
  });
});

afterEach(() => vi.clearAllMocks());

describe('#910a — per-provider busy + edit isolation', () => {
  it('saving one card leaves other cards interactive and preserves their edits', async () => {
    const user = userEvent.setup({ delay: null });

    // Hold the save pending so we can observe the in-flight state.
    let resolveSave: (v: unknown) => void = () => {};
    vi.mocked(chatApi.updateAdminChatConfig).mockImplementation(
      () => new Promise((res) => { resolveSave = res as (v: unknown) => void; }),
    );

    render(<ChatbotSettingsTab />);
    await screen.findByText('Anthropic');

    const anthropic = await cardFor('Anthropic');
    const openai = await cardFor('OpenAI');

    // Put an in-progress edit on the OpenAI card.
    const openaiModel = openai.getByDisplayValue('gpt-4o-mini');
    await user.clear(openaiModel);
    await user.type(openaiModel, 'gpt-4o-EDITED');

    // Make the Anthropic card dirty and save it (stays pending).
    const anthropicModel = anthropic.getByDisplayValue('claude-sonnet-4-6');
    await user.type(anthropicModel, '-x');
    await user.click(anthropic.getByRole('button', { name: 'Save' }));

    // While Anthropic's save is in flight, the OpenAI card must remain usable
    // (this is the #910a bug: a global `busy` disabled every card's buttons).
    expect(openai.getByRole('button', { name: /test connection/i })).toBeEnabled();

    // Resolve the save with a config that reflects ONLY anthropic's change.
    const updated = makeConfig();
    updated.providers.anthropic.model = 'claude-sonnet-4-6-x';
    resolveSave(updated);

    // OpenAI's unsaved edit must survive Anthropic's save completing
    // (handleSaveCard must not rebuild every card's draft).
    await waitFor(() => {
      expect(openai.getByDisplayValue('gpt-4o-EDITED')).toBeInTheDocument();
    });
  });
});

describe('#910b — radio/select-only change is detected and sent', () => {
  it('Bedrock auth_mode change alone (null-saved row) is dirty and PATCHed', async () => {
    const user = userEvent.setup({ delay: null });
    render(<ChatbotSettingsTab />);
    await screen.findByText('AWS Bedrock');
    const bedrock = await cardFor('AWS Bedrock');

    // Toggle away then back to the shown default. Selecting the *current*
    // value fires no change event, so this away-and-back is the minimal real
    // interaction; on a null-saved row it must still count as a change.
    await user.click(bedrock.getByLabelText('Assume role'));
    await user.click(bedrock.getByLabelText('Access keys'));

    const saveBtn = bedrock.getByRole('button', { name: 'Save' });
    expect(saveBtn).toBeEnabled();
    await user.click(saveBtn);

    await waitFor(() => expect(vi.mocked(chatApi.updateAdminChatConfig)).toHaveBeenCalled());
    const body = vi.mocked(chatApi.updateAdminChatConfig).mock.calls[0][0];
    expect(body.providers?.aws_bedrock?.auth_mode).toBe('access_keys');
  });

  it('Foundry protocol change alone (null-saved row) is dirty and PATCHed', async () => {
    const user = userEvent.setup({ delay: null });
    render(<ChatbotSettingsTab />);
    await screen.findByText('Microsoft Foundry');
    const foundry = await cardFor('Microsoft Foundry');

    const select = foundry.getByDisplayValue('Anthropic Messages');
    await user.selectOptions(select, 'openai_chat');
    await user.selectOptions(select, 'anthropic'); // back to the default

    const saveBtn = foundry.getByRole('button', { name: 'Save' });
    expect(saveBtn).toBeEnabled();
    await user.click(saveBtn);

    await waitFor(() => expect(vi.mocked(chatApi.updateAdminChatConfig)).toHaveBeenCalled());
    const body = vi.mocked(chatApi.updateAdminChatConfig).mock.calls[0][0];
    expect(body.providers?.foundry?.foundry_protocol).toBe('anthropic');
  });
});

describe('#911 — Test disabled until required fields filled', () => {
  it('enables Test only once required fields are present (OpenAI-compatible)', async () => {
    const user = userEvent.setup({ delay: null });
    render(<ChatbotSettingsTab />);
    await screen.findByText('OpenAI-compatible');
    const compat = await cardFor('OpenAI-compatible');

    const testBtn = compat.getByRole('button', { name: /test connection/i });
    expect(testBtn).toBeDisabled(); // empty card

    await user.type(compat.getByPlaceholderText(/llama-3.1-8b-instruct/i), 'llama-3.1-8b');
    expect(testBtn).toBeDisabled(); // still missing key + base_url

    await user.click(compat.getByTitle('Edit')); // open API-key editor
    await user.type(compat.getByPlaceholderText('Paste API key'), 'sk-compat');
    expect(testBtn).toBeDisabled(); // still missing base_url

    await user.type(compat.getByPlaceholderText(/Full URL including/i), 'http://host:8000/v1');
    expect(testBtn).toBeEnabled(); // all required fields present
  });

  it('treats a stored, unchanged API key as filled (Anthropic Test enabled)', async () => {
    render(<ChatbotSettingsTab />);
    await screen.findByText('Anthropic');
    const anthropic = await cardFor('Anthropic');
    // model set + api_key_configured true, key mode 'unchanged' → Test enabled.
    expect(anthropic.getByRole('button', { name: /test connection/i })).toBeEnabled();
  });
});

describe('#912 enable-gate + disable-all, #909 test modal, #3 no toast', () => {
  it('enables an inactive provider only after a successful Test, then persists (no toast)', async () => {
    const user = userEvent.setup({ delay: null });
    vi.mocked(chatApi.testAdminChatConfig).mockResolvedValue({
      ok: true, detail: 'ok', sample_text: 'Hi there',
      provider: 'openai', model: 'gpt-4o-mini',
    } as never);
    vi.mocked(chatApi.updateAdminChatConfig).mockResolvedValueOnce({
      ...makeConfig(), active_provider: 'openai',
    });
    render(<ChatbotSettingsTab />);
    await screen.findByText('OpenAI');
    const openai = await cardFor('OpenAI');

    // Configured but not tested this session → enable toggle disabled.
    const toggle = openai.getByRole('switch');
    expect(toggle).toBeDisabled();

    // Test → green success flash (text only: no Close button, no inline line).
    await user.click(openai.getByRole('button', { name: /test connection/i }));
    await screen.findByText('Connection successful');
    expect(screen.queryByText(/✓ Test successful/)).toBeNull();
    expect(screen.queryByRole('button', { name: /close/i })).toBeNull();

    // Now enable-able; toggling on persists and shows NO success banner (#3).
    expect(toggle).toBeEnabled();
    await user.click(toggle);
    await waitFor(() => {
      expect(vi.mocked(chatApi.updateAdminChatConfig)).toHaveBeenCalledWith({
        active_provider: 'openai',
      });
    });
    expect(screen.queryByText(/active provider switched/i)).toBeNull();
  });

  it('toggling the active provider off disables chat and shows the banner (#912)', async () => {
    const user = userEvent.setup({ delay: null });
    vi.mocked(chatApi.updateAdminChatConfig).mockResolvedValueOnce({
      ...makeConfig(), active_provider: null,
    });
    render(<ChatbotSettingsTab />);
    await screen.findByText('Anthropic');
    const anthropic = await cardFor('Anthropic');

    await user.click(anthropic.getAllByRole('switch')[0]);
    await waitFor(() => {
      expect(vi.mocked(chatApi.updateAdminChatConfig)).toHaveBeenCalledWith({
        active_provider: null,
      });
    });
    expect(await screen.findByText(/no provider enabled/i)).toBeInTheDocument();
  });

  it('a passing test turns the clicked Test button green ("Success"), then reverts on its own', async () => {
    const user = userEvent.setup({ delay: null });
    vi.mocked(chatApi.testAdminChatConfig).mockResolvedValue({
      ok: true, detail: 'ok', sample_text: 'hi',
      provider: 'anthropic', model: 'x',
    } as never);
    render(<ChatbotSettingsTab />);
    await screen.findByText('Anthropic');
    const anthropic = await cardFor('Anthropic');

    const testBtn = anthropic.getByRole('button', { name: /test connection/i });
    await user.click(testBtn);
    // The clicked button carries the confirmation (no page-wide flash pane) …
    await waitFor(() => expect(testBtn).toHaveTextContent('Success'));
    expect(document.querySelector('[class*="flashPane"]')).toBeNull();
    // … and reverts to its label without any user action.
    await waitFor(() => expect(testBtn).toHaveTextContent(/test connection/i), { timeout: 3000 });
  });

  it('shows a save failure in a modal (raw message), not a top banner', async () => {
    const user = userEvent.setup({ delay: null });
    vi.mocked(chatApi.updateAdminChatConfig).mockRejectedValueOnce(
      new Error('boom-save'),
    );
    render(<ChatbotSettingsTab />);
    await screen.findByText('OpenAI');
    const openai = await cardFor('OpenAI');

    await user.type(openai.getByDisplayValue('gpt-4o-mini'), '-x'); // dirty
    await user.click(openai.getByRole('button', { name: 'Save' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Save failed')).toBeInTheDocument();
    expect(within(dialog).getByText(/boom-save/)).toBeInTheDocument();
  });

  it('shows the failure modal when a Test fails (#909)', async () => {
    const user = userEvent.setup({ delay: null });
    vi.mocked(chatApi.testAdminChatConfig).mockResolvedValue({
      ok: false, detail: 'invalid api key', sample_text: '',
      provider: 'anthropic', model: 'x',
    } as never);
    render(<ChatbotSettingsTab />);
    await screen.findByText('Anthropic');
    const anthropic = await cardFor('Anthropic');

    await user.click(anthropic.getByRole('button', { name: /test connection/i }));
    expect(await screen.findByText('Connection test failed')).toBeInTheDocument();
    expect(screen.getByText('invalid api key')).toBeInTheDocument();
  });
});
