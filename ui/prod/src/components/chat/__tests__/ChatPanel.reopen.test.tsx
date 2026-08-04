// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React from 'react';

// Mock only the data hooks — ChatProvider stays REAL so localStorage
// persistence and the open/currentSessionId state are exercised for real.
const mockCreateMutate = vi.fn();

vi.mock('../hooks/useChatSession', () => ({
  useChatSession: () => ({
    session: {
      id: 'sess-1', title: 'My chat',
      model: 'm', provider: 'p', created_at: 't', updated_at: 't',
    },
    messages: [{ id: 'm0', role: 'user', content: 'prior', tool_calls: null }],
    isLoading: false, isError: false, streaming: false,
    assistantText: '', toolCalls: [], error: null, currentMessageId: null,
    send: vi.fn(), cancel: vi.fn(),
  }),
}));

vi.mock('../hooks/useConversationHistory', () => ({
  useConversationHistory: () => ({
    sessions: [{
      id: 'sess-1', title: 'My chat',
      model: 'm', provider: 'p', created_at: 't', updated_at: 't',
    }],
    total: 1, isLoading: false, isError: false,
    create: { mutate: mockCreateMutate, isPending: false, isError: false },
    rename: { mutate: vi.fn(), isPending: false },
    remove: { mutate: vi.fn() },
    autotitle: { mutate: vi.fn() },
  }),
}));

vi.mock('@/components/charts/DotDNAChart', () => ({
  DotDNAChart: () => React.createElement('div', { 'data-testid': 'dotdna-chart-stub' }),
}));

import { ChatProvider } from '../ChatProvider';
import { ChatBubble } from '../ChatBubble';
import { ChatPanel } from '../ChatPanel';

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

function renderWidget() {
  return render(
    <ChatProvider>
      <ChatBubble />
      <ChatPanel />
    </ChatProvider>,
  );
}

describe('reopen resumes the active conversation (issue 2)', () => {
  it('does not auto-create a new conversation when reopening a persisted session', async () => {
    // Persisted UI state: panel open on session sess-1.
    localStorage.setItem(
      'chat_ui_state',
      JSON.stringify({ open: true, currentSessionId: 'sess-1' }),
    );
    renderWidget();

    // The active conversation is shown (its title), not a fresh chat.
    expect(screen.getByText('My chat')).toBeInTheDocument();
    expect(mockCreateMutate).not.toHaveBeenCalled();

    // Minimize via the bubble (aria-pressed=true disambiguates it from the
    // panel's own close button, which has no aria-pressed).
    await act(async () => {
      fireEvent.click(
        screen.getByRole('button', { name: 'Close chat', pressed: true }),
      );
    });

    // Minimizing unmounts the panel body (ChatPanel returns null when closed),
    // so the title is gone now — proving the reopen below genuinely restores
    // it instead of the final assertion passing vacuously.
    expect(screen.queryByText('My chat')).not.toBeInTheDocument();

    // Reopen via the bubble.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Open chat' }));
    });

    // Same conversation resumes; still no auto-create.
    expect(screen.getByText('My chat')).toBeInTheDocument();
    expect(mockCreateMutate).not.toHaveBeenCalled();
  });
});
