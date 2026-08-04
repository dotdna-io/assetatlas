// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ChatBehaviorSettingsTab } from '../ChatBehaviorSettingsTab';
import { apiClient } from '../../../api/client';

// Mock the raw API client (used by CategorySettings internals).
vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn(), put: vi.fn(), post: vi.fn(), del: vi.fn() },
}));

// CategorySettings makes two GETs: /settings/:category and /settings/schema.
// The chat category holds three settings — active_provider is hidden (owned by
// the provider Configuration tab) while the two routing knobs are visible.
const MOCK_SETTINGS = {
  settings: {
    active_provider: { value: 'anthropic' },
    tool_routing_enabled: { value: false },
    tool_routing_top_k: { value: 5 },
  },
};
const MOCK_SCHEMA = {
  schema: {
    chat: {
      active_provider: {
        type: 'string',
        default: 'anthropic',
        description: 'Active chat LLM provider.',
        options: ['anthropic', 'openai'],
        hidden: true,
      },
      tool_routing_enabled: {
        type: 'boolean',
        default: false,
        description: 'Advertise only a core tool set + question-relevant tools.',
        hidden: false,
      },
      tool_routing_top_k: {
        type: 'integer',
        default: 5,
        min: 1,
        max: 20,
        description: 'How many question-relevant gated tools to advertise per turn.',
        hidden: false,
      },
    },
  },
};

function setupApiMocks() {
  (apiClient.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
    if (url.includes('/schema')) return Promise.resolve(MOCK_SCHEMA);
    return Promise.resolve(MOCK_SETTINGS);
  });
}

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe('ChatBehaviorSettingsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupApiMocks();
  });

  it('renders the visible routing settings', async () => {
    wrap(<ChatBehaviorSettingsTab />);
    expect(await screen.findByText('Tool Routing Enabled')).toBeInTheDocument();
    expect(screen.getByText('Tool Routing Top K')).toBeInTheDocument();
  });

  it('does not render the hidden active_provider setting (owned by the Configuration tab)', async () => {
    wrap(<ChatBehaviorSettingsTab />);
    // Wait for the form to render before asserting absence.
    await screen.findByText('Tool Routing Enabled');
    expect(screen.queryByText('Active Provider')).not.toBeInTheDocument();
  });
});
