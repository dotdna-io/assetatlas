// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

// Mutable across tests; the mock factories read them at call time.
let mockCurrentSessionId: string | null = null;
let mockSessions: any[] = [];
let mockIsLoading = false;
let mockCreateIsError = false;
const mockSetCurrentSessionId = vi.fn();
const mockCreate = vi.fn();

vi.mock('../ChatProvider', () => ({
  useChatUI: () => ({
    open: true,
    setOpen: vi.fn(),
    currentSessionId: mockCurrentSessionId,
    setCurrentSessionId: mockSetCurrentSessionId,
  }),
}));

vi.mock('../hooks/useChatSession', () => ({
  useChatSession: () => ({
    session: null, messages: [], isLoading: false, isError: false,
    streaming: false, assistantText: '', toolCalls: [], error: null,
    currentMessageId: null, send: vi.fn(), cancel: vi.fn(),
  }),
}));

vi.mock('../hooks/useConversationHistory', () => ({
  useConversationHistory: () => ({
    sessions: mockSessions,
    total: mockSessions.length, isLoading: mockIsLoading, isError: false,
    create: {
      mutate: mockCreate,
      isPending: false,
      isError: mockCreateIsError,
      error: mockCreateIsError ? new Error('backend down') : null,
    },
    rename: { mutate: vi.fn(), isPending: false },
    remove: { mutate: vi.fn() },
    autotitle: { mutate: vi.fn() },
  }),
}));

vi.mock('@/components/charts/DotDNAChart', () => ({
  DotDNAChart: () => React.createElement('div', { 'data-testid': 'dotdna-chart-stub' }),
}));

import { ChatPanel } from '../ChatPanel';

beforeEach(() => {
  vi.clearAllMocks();
  mockCurrentSessionId = null;
  mockSessions = [];
  mockIsLoading = false;
  mockCreateIsError = false;
});

describe('ChatPanel reopen landing (issue 2)', () => {
  it('shows the conversation list (no auto-create) when no session is active and history exists', () => {
    mockCurrentSessionId = null;
    mockSessions = [{
      id: 's1', title: 'Asset Counts',
      model: 'm', provider: 'p', created_at: 't', updated_at: 't',
    }];
    render(<ChatPanel />);
    expect(screen.getByText('Asset Counts')).toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it('shows a start-conversation action (no auto-create) when history is empty', () => {
    mockCurrentSessionId = null;
    mockSessions = [];
    render(<ChatPanel />);
    const startBtn = screen.getByRole('button', { name: /start a conversation/i });
    expect(mockCreate).not.toHaveBeenCalled();
    fireEvent.click(startBtn);
    expect(mockCreate).toHaveBeenCalled();
  });

  it('shows the conversation (not the list) when a session is active', () => {
    mockCurrentSessionId = 'sess-1';
    mockSessions = [{
      id: 'sess-1', title: 'Asset Counts',
      model: 'm', provider: 'p', created_at: 't', updated_at: 't',
    }];
    render(<ChatPanel />);
    // Empty messages + active session → the WelcomePanel, not the list landing.
    expect(screen.getByText(/Ask about your assets/i)).toBeInTheDocument();
  });

  it('shows a loading message (not empty-state) while history is loading', () => {
    mockCurrentSessionId = null;
    mockSessions = [];
    mockIsLoading = true;
    render(<ChatPanel />);
    expect(screen.getByText(/loading conversations/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /start a conversation/i })).not.toBeInTheDocument();
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it('keeps showing the conversation list when a previous create failed', () => {
    // A stale create error must not hide existing conversations — the user
    // should still see and pick from their history.
    mockCurrentSessionId = null;
    mockCreateIsError = true;
    mockSessions = [{
      id: 's1', title: 'Asset Counts',
      model: 'm', provider: 'p', created_at: 't', updated_at: 't',
    }];
    render(<ChatPanel />);
    expect(screen.getByText('Asset Counts')).toBeInTheDocument();
    expect(
      screen.queryByText(/Couldn't start a new conversation/i),
    ).not.toBeInTheDocument();
  });
});
