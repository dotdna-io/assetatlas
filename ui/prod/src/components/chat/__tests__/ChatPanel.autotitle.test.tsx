// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * P4 auto-title decoupling (#735)
 *
 * Verifies that history.autotitle.mutate is called regardless of whether
 * session.send() returns 'ok' or a non-ok outcome ('error', 'aborted').
 * Before the fix, auto-title was gated on `outcome === 'ok'`, so a 500/error
 * turn left the session title stuck at "Untitled conversation" forever.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React from 'react';

// ── Mock the three hooks ChatPanel consumes ────────────────────────────────
// Must be declared before the ChatPanel import (vi.mock is hoisted).

vi.mock('../ChatProvider', () => ({
  useChatUI: () => ({
    open: true,
    setOpen: vi.fn(),
    currentSessionId: 'sess-1',
    setCurrentSessionId: vi.fn(),
  }),
}));

// useChatSession — session.send is what we vary across tests; set via mockSend
const mockSend = vi.fn();

vi.mock('../hooks/useChatSession', () => ({
  useChatSession: () => ({
    session: { id: 'sess-1', title: 'Untitled conversation' },
    messages: [{ id: 'm0', role: 'user', content: 'prior', tool_calls: null }],
    isLoading: false,
    isError: false,
    streaming: false,
    assistantText: '',
    toolCalls: [],
    error: null,
    currentMessageId: null,
    send: mockSend,
    cancel: vi.fn(),
  }),
}));

// useConversationHistory — capture autotitle.mutate
const mockAutotitleMutate = vi.fn();

vi.mock('../hooks/useConversationHistory', () => ({
  useConversationHistory: () => ({
    sessions: [],
    total: 0,
    isLoading: false,
    isError: false,
    create: { mutate: vi.fn(), isPending: false, isError: false },
    rename: { mutate: vi.fn(), isPending: false },
    remove: { mutate: vi.fn() },
    autotitle: { mutate: mockAutotitleMutate },
  }),
}));

// Stub the chart so it renders without DotDNA globals
vi.mock('@/components/charts/DotDNAChart', () => ({
  DotDNAChart: () => React.createElement('div', { 'data-testid': 'dotdna-chart-stub' }),
}));

import { ChatPanel } from '../ChatPanel';

// ── helpers ────────────────────────────────────────────────────────────────

function renderPanel() {
  return render(<ChatPanel />);
}

function typeAndSend(text: string) {
  const ta = screen.getByRole('textbox', { name: /chat message/i }) as HTMLTextAreaElement;
  fireEvent.change(ta, { target: { value: text } });
  fireEvent.keyDown(ta, { key: 'Enter' });
}

// ── tests ──────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
});

describe('P4 auto-title decoupling (#735)', () => {
  it('fires auto-title when the turn succeeds (ok)', async () => {
    mockSend.mockResolvedValue('ok');
    renderPanel();

    await act(async () => {
      typeAndSend('hello ok');
    });

    expect(mockSend).toHaveBeenCalledWith('hello ok');
    expect(mockAutotitleMutate).toHaveBeenCalledWith(
      expect.objectContaining({ userText: 'hello ok', id: 'sess-1' }),
      expect.anything(),
    );
  });

  it('fires auto-title even when the turn fails (error outcome)', async () => {
    // Core P4 regression: before the fix, outcome !== 'ok' blocked
    // autotitle.mutate from being called at all — title stuck at default.
    mockSend.mockResolvedValue('error');
    renderPanel();

    await act(async () => {
      typeAndSend('hello error');
    });

    expect(mockSend).toHaveBeenCalledWith('hello error');
    expect(mockAutotitleMutate).toHaveBeenCalledWith(
      expect.objectContaining({ userText: 'hello error', id: 'sess-1' }),
      expect.anything(),
    );
  });

  it('fires auto-title even when the turn is aborted', async () => {
    mockSend.mockResolvedValue('aborted');
    renderPanel();

    await act(async () => {
      typeAndSend('hello aborted');
    });

    expect(mockSend).toHaveBeenCalledWith('hello aborted');
    expect(mockAutotitleMutate).toHaveBeenCalledWith(
      expect.objectContaining({ userText: 'hello aborted', id: 'sess-1' }),
      expect.anything(),
    );
  });
});
