// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Unit tests for the Microsoft Foundry card on ChatbotSettingsTab.
 *
 * Mocks the chat API and asserts on:
 *  - the protocol dropdown renders all three options
 *  - Save sends `foundry_protocol` in the patch body
 *  - Test sends `foundry_protocol` in the test request body
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
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

const BASE_CONFIG = {
  active_provider: 'anthropic' as const,
  chat_enabled: true,
  providers: {
    anthropic: {
      ...EMPTY_PROVIDER,
      provider: 'anthropic' as const,
      model: '',
    },
    openai: {
      ...EMPTY_PROVIDER,
      provider: 'openai' as const,
      model: '',
    },
    openai_compat: {
      ...EMPTY_PROVIDER,
      provider: 'openai_compat' as const,
      model: '',
    },
    aws_bedrock: {
      ...EMPTY_PROVIDER,
      provider: 'aws_bedrock' as const,
      model: '',
    },
    foundry: {
      ...EMPTY_PROVIDER,
      provider: 'foundry' as const,
      model: '',
      foundry_protocol: null,
    },
  },
};

beforeEach(() => {
  vi.mocked(chatApi.getAdminChatConfig).mockResolvedValue(BASE_CONFIG);
  vi.mocked(chatApi.getBedrockModels).mockResolvedValue({
    models: [],
    source: 'fallback',
  });
  vi.mocked(chatApi.updateAdminChatConfig).mockResolvedValue(BASE_CONFIG);
  vi.mocked(chatApi.testAdminChatConfig).mockResolvedValue({
    ok: true,
    detail: 'ok',
    sample_text: 'OK',
    provider: 'foundry',
    model: 'claude-sonnet-4-6',
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('ChatbotSettingsTab — Microsoft Foundry card', () => {
  it('renders the protocol dropdown with three options', async () => {
    render(<ChatbotSettingsTab />);
    await screen.findByText('Microsoft Foundry');

    // The protocol <select> defaults to 'anthropic' which renders as
    // 'Anthropic Messages'. Find it via its display value.
    const select = await screen.findByDisplayValue('Anthropic Messages') as HTMLSelectElement;
    expect(select).toBeInTheDocument();

    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toEqual(['anthropic', 'openai_chat', 'openai_responses']);
  });

  it('Save sends foundry_protocol + base_url in the patch body', async () => {
    const user = userEvent.setup({ delay: null });
    render(<ChatbotSettingsTab />);
    await screen.findByText('Microsoft Foundry');

    // Scope to the Foundry card to avoid ambiguous queries across cards.
    const foundryHeading = screen.getByText('Microsoft Foundry');
    const foundryCardEl = foundryHeading.closest(
      `.${(await import('../ChatbotSettingsTab.module.css')).default.providerCard}`,
    ) as HTMLElement;
    expect(foundryCardEl).not.toBeNull();
    const foundryCard = within(foundryCardEl);

    // Change the protocol select to openai_responses.
    const select = foundryCard.getByDisplayValue('Anthropic Messages');
    await user.selectOptions(select, 'openai_responses');

    // Type a Target URL.
    const urlInput = foundryCard.getByPlaceholderText(/anthropic\/v1\/messages/);
    await user.type(urlInput, 'https://example.azure.com/openai/responses?api-version=v');

    // Save the card.
    await user.click(foundryCard.getByText('Save'));

    await waitFor(() => {
      expect(vi.mocked(chatApi.updateAdminChatConfig)).toHaveBeenCalled();
    });
    const body = vi.mocked(chatApi.updateAdminChatConfig).mock.calls[0][0];
    expect(body.providers?.foundry?.foundry_protocol).toBe('openai_responses');
    expect(body.providers?.foundry?.base_url).toContain('api-version=v');
  });

  it('first save on a fresh foundry row persists the default protocol', async () => {
    // Simulates the trap: the user keeps the dropdown on its default value
    // (Anthropic Messages) but fills URL + API key. The PATCH body must
    // include foundry_protocol so the backend persists it; without that,
    // toggling foundry active later fails with "protocol is not set".
    const user = userEvent.setup({ delay: null });
    render(<ChatbotSettingsTab />);
    await screen.findByText('Microsoft Foundry');

    const foundryHeading = screen.getByText('Microsoft Foundry');
    const foundryCardEl = foundryHeading.closest(
      `.${(await import('../ChatbotSettingsTab.module.css')).default.providerCard}`,
    ) as HTMLElement;
    const foundryCard = within(foundryCardEl);

    // User does NOT change the protocol dropdown — keeps default 'anthropic'.
    // Fill in Target URL.
    const urlInput = foundryCard.getByPlaceholderText(/anthropic\/v1\/messages/);
    await user.type(urlInput, 'https://x/anthropic/v1/messages');

    // Click Edit on the API key field, then type a key.
    const editKeyBtn = foundryCard.getByTitle('Edit');
    await user.click(editKeyBtn);
    const keyInput = foundryCard.getByPlaceholderText('Paste API key');
    await user.type(keyInput, 'sk-foundry-key');

    const saveBtn = foundryCard.getByText('Save');
    await user.click(saveBtn);

    await waitFor(() => expect(vi.mocked(chatApi.updateAdminChatConfig)).toHaveBeenCalled());
    const body = vi.mocked(chatApi.updateAdminChatConfig).mock.calls[0][0];
    // The default protocol MUST be included in the patch body even though
    // the dropdown value didn't change.
    expect(body.providers?.foundry?.foundry_protocol).toBe('anthropic');
  });

  it('Test sends foundry_protocol in the request body', async () => {
    const user = userEvent.setup({ delay: null });
    render(<ChatbotSettingsTab />);
    await screen.findByText('Microsoft Foundry');

    // Scope to the Foundry card.
    const foundryHeading = screen.getByText('Microsoft Foundry');
    const foundryCardEl = foundryHeading.closest(
      `.${(await import('../ChatbotSettingsTab.module.css')).default.providerCard}`,
    ) as HTMLElement;
    const foundryCard = within(foundryCardEl);

    // #911: Test is disabled until the required fields are present, so fill
    // model + API key + Target URL before testing.
    await user.type(foundryCard.getByPlaceholderText('claude-sonnet-4-6'), 'claude-x');
    await user.click(foundryCard.getByTitle('Edit'));
    await user.type(foundryCard.getByPlaceholderText('Paste API key'), 'sk-foundry');
    await user.type(
      foundryCard.getByPlaceholderText(/anthropic\/v1\/messages/),
      'https://x/anthropic/v1/messages',
    );

    // Change protocol to openai_chat.
    const select = foundryCard.getByDisplayValue('Anthropic Messages');
    await user.selectOptions(select, 'openai_chat');

    // Click "Test connection" — no Save needed; test uses the in-memory draft.
    await user.click(foundryCard.getByRole('button', { name: /test connection/i }));

    await waitFor(() => {
      expect(vi.mocked(chatApi.testAdminChatConfig)).toHaveBeenCalled();
    });
    const body = vi.mocked(chatApi.testAdminChatConfig).mock.calls[0][0];
    expect(body.provider).toBe('foundry');
    expect(body.foundry_protocol).toBe('openai_chat');
  });
});
