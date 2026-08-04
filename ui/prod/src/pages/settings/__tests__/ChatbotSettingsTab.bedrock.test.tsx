// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
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
      model: 'claude-sonnet-4-6',
      api_key_preview: '••••abcd',
      api_key_configured: true,
    },
    openai: {
      ...EMPTY_PROVIDER,
      provider: 'openai' as const,
      model: 'gpt-4o-mini',
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
    },
  },
};

function makeConfig(overrides: Partial<typeof BASE_CONFIG> = {}) {
  return { ...BASE_CONFIG, ...overrides };
}

beforeEach(() => {
  vi.mocked(chatApi.getAdminChatConfig).mockResolvedValue(BASE_CONFIG);
  vi.mocked(chatApi.getBedrockModels).mockResolvedValue({
    models: [
      {
        id: 'anthropic.claude-3-5-sonnet-20241022-v2:0',
        name: 'Claude 3.5 Sonnet',
        provider: 'Anthropic',
      },
    ],
    source: 'live',
  });
  vi.mocked(chatApi.updateAdminChatConfig).mockResolvedValue(BASE_CONFIG);
});

describe('ChatbotSettingsTab AWS Bedrock card', () => {
  it('renders auth-mode radios with access_keys checked by default', async () => {
    render(<ChatbotSettingsTab />);
    await screen.findByText('AWS Bedrock');

    const accessRadio = screen.getByLabelText('Access keys') as HTMLInputElement;
    const roleRadio = screen.getByLabelText('Assume role') as HTMLInputElement;
    expect(accessRadio.checked).toBe(true);
    expect(roleRadio.checked).toBe(false);
    expect(screen.getByPlaceholderText('us-east-1')).toBeInTheDocument();
    // role-ARN field should NOT be visible yet
    expect(
      screen.queryByPlaceholderText('arn:aws:iam::123456789012:role/BedrockAccess'),
    ).not.toBeInTheDocument();
  });

  it('swaps to role-ARN field when "Assume role" is selected', async () => {
    const user = userEvent.setup();
    render(<ChatbotSettingsTab />);
    await screen.findByText('AWS Bedrock');

    await user.click(screen.getByLabelText('Assume role'));
    expect(
      screen.getByPlaceholderText('arn:aws:iam::123456789012:role/BedrockAccess'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Access Key ID')).not.toBeInTheDocument();
    expect(screen.queryByText('Secret Access Key')).not.toBeInTheDocument();
  });

  it('does NOT show the "backend support pending" tooltip', async () => {
    render(<ChatbotSettingsTab />);
    await screen.findByText('AWS Bedrock');
    expect(screen.queryByText(/backend support pending/i)).not.toBeInTheDocument();
  });

  it('falls back to curated list and shows the hint when getBedrockModels returns fallback', async () => {
    vi.mocked(chatApi.getBedrockModels).mockResolvedValueOnce({
      models: [],
      source: 'fallback',
      error: 'boom',
    });
    const cfg = makeConfig({
      providers: {
        ...BASE_CONFIG.providers,
        aws_bedrock: {
          ...BASE_CONFIG.providers.aws_bedrock,
          aws_region: 'us-east-1',
        },
      },
    });
    vi.mocked(chatApi.getAdminChatConfig).mockResolvedValueOnce(cfg);

    render(<ChatbotSettingsTab />);
    await screen.findByText('AWS Bedrock');

    await waitFor(() => {
      expect(screen.getByText(/couldn[’']t reach AWS/i)).toBeInTheDocument();
    });
  });

  it('sends auth_mode + access_key_id + secret_access_key on first save', async () => {
    const user = userEvent.setup();
    render(<ChatbotSettingsTab />);
    await screen.findByText('AWS Bedrock');

    // Scope to the Bedrock card so future provider reordering doesn't
    // silently click the wrong fields.
    const bedrockHeading = screen.getByText('AWS Bedrock');
    const bedrockCardEl = bedrockHeading.closest(
      `.${(await import('../ChatbotSettingsTab.module.css')).default.providerCard}`,
    ) as HTMLElement;
    expect(bedrockCardEl).not.toBeNull();
    const bedrockCard = within(bedrockCardEl);

    // Set region.
    await user.type(bedrockCard.getByPlaceholderText('us-east-1'), 'us-east-1');

    // Open access-key editor inside the bedrock card and type.
    const accessKeyEdit = bedrockCard.getAllByTitle('Edit')[0];
    await user.click(accessKeyEdit);
    await user.type(bedrockCard.getByPlaceholderText('AKIA...'), 'AKIATEST');

    // Open secret editor.
    const secretEdit = bedrockCard.getAllByTitle('Edit')[0]; // first Edit now points to the secret control
    await user.click(secretEdit);
    await user.type(bedrockCard.getByPlaceholderText('Paste secret access key'), 'sktest');

    // Click Save on the bedrock card.
    await user.click(bedrockCard.getByText('Save'));

    await waitFor(() => {
      expect(vi.mocked(chatApi.updateAdminChatConfig)).toHaveBeenCalled();
    });
    const body = vi.mocked(chatApi.updateAdminChatConfig).mock.calls[0][0];
    expect(body.providers?.aws_bedrock).toEqual(expect.objectContaining({
      aws_region: 'us-east-1',
      auth_mode: 'access_keys',
      access_key_id: 'AKIATEST',
      secret_access_key: 'sktest',
    }));
  });
});

describe('ChatbotSettingsTab active-provider toggle', () => {
  async function cardEl(label: string) {
    return screen.getByText(label).closest(
      `.${(await import('../ChatbotSettingsTab.module.css')).default.providerCard}`,
    ) as HTMLElement;
  }

  it('toggling the active provider off disables every provider (#912)', async () => {
    // BASE_CONFIG has anthropic active. Clicking its toggle turns chat off
    // entirely — a PATCH with active_provider: null.
    const user = userEvent.setup();
    vi.mocked(chatApi.updateAdminChatConfig).mockResolvedValueOnce({
      ...BASE_CONFIG, active_provider: null,
    });
    render(<ChatbotSettingsTab />);
    await screen.findByText('Anthropic');

    const toggle = within(await cardEl('Anthropic')).getAllByRole('switch')[0];
    await user.click(toggle);

    await waitFor(() => {
      expect(vi.mocked(chatApi.updateAdminChatConfig)).toHaveBeenCalledWith({
        active_provider: null,
      });
    });
  });

  it('an unconfigured, untested provider cannot be enabled (toggle disabled)', async () => {
    // Bedrock has no region/credentials in BASE_CONFIG and hasn't been
    // tested this session, so its enable toggle is disabled (#912).
    render(<ChatbotSettingsTab />);
    await screen.findByText('AWS Bedrock');
    const toggle = within(await cardEl('AWS Bedrock')).getByRole('switch');
    expect(toggle).toBeDisabled();
  });

  it('rolls the toggle back when the deactivation save fails', async () => {
    const user = userEvent.setup();
    vi.mocked(chatApi.updateAdminChatConfig).mockRejectedValueOnce(
      new Error('backend blew up'),
    );
    render(<ChatbotSettingsTab />);
    await screen.findByText('Anthropic');

    const toggle = within(await cardEl('Anthropic')).getAllByRole('switch')[0];
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    await user.click(toggle);  // optimistic off, then rollback on failure

    await waitFor(() => {
      expect(toggle.getAttribute('aria-checked')).toBe('true');
    });
  });
});
