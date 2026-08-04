// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

import { ConversationList, groupSessions } from '../ConversationList';
import type { ChatSession } from '../../../api/chat';

function session(overrides: Partial<ChatSession> = {}): ChatSession {
  return {
    id: 's1',
    title: 'Hello',
    model: 'claude-opus-4-7',
    provider: 'anthropic',
    created_at: '2026-04-22T10:00:00Z',
    updated_at: '2026-04-22T10:00:00Z',
    ...overrides,
  };
}

describe('groupSessions', () => {
  const now = new Date('2026-04-22T12:00:00Z');
  it('buckets by updated_at', () => {
    const out = groupSessions(
      [
        session({ id: 'a', updated_at: '2026-04-22T08:00:00Z' }),
        session({ id: 'b', updated_at: '2026-04-21T08:00:00Z' }),
        session({ id: 'c', updated_at: '2026-04-18T08:00:00Z' }),
      ],
      now,
    );
    expect(out.Today.map((s) => s.id)).toEqual(['a']);
    expect(out.Yesterday.map((s) => s.id)).toEqual(['b']);
    expect(out.Earlier.map((s) => s.id)).toEqual(['c']);
  });

  it('tolerates missing/unparseable timestamps', () => {
    const out = groupSessions(
      [
        session({ id: 'x', created_at: '', updated_at: '' }),
        session({ id: 'y', created_at: 'bogus', updated_at: 'bogus' }),
      ],
      now,
    );
    // no timestamp -> treated as "now" -> Today; unparseable -> Earlier
    expect(out.Today.map((s) => s.id)).toEqual(['x']);
    expect(out.Earlier.map((s) => s.id)).toEqual(['y']);
  });
});

describe('ConversationList', () => {
  const base = {
    sessions: [] as ChatSession[],
    currentSessionId: null as string | null,
    onSelect: vi.fn(),
    onNew: vi.fn(),
    onRename: vi.fn(),
    onDelete: vi.fn(),
  };

  it('shows empty state when no sessions', () => {
    render(<ConversationList {...base} />);
    expect(screen.getByText(/no conversations yet/i)).toBeInTheDocument();
  });

  it('fires onNew when + is clicked', () => {
    const onNew = vi.fn();
    render(<ConversationList {...base} onNew={onNew} />);
    fireEvent.click(screen.getByLabelText('New conversation'));
    expect(onNew).toHaveBeenCalled();
  });

  it('renders grouped sessions and calls onSelect on click', () => {
    const sessions = [
      session({ id: 'a', title: 'First', updated_at: new Date().toISOString() }),
    ];
    const onSelect = vi.fn();
    render(<ConversationList {...base} sessions={sessions} onSelect={onSelect} />);
    expect(screen.getByText('First')).toBeInTheDocument();
    expect(screen.getByText('Today')).toBeInTheDocument();
    fireEvent.click(screen.getByText('First'));
    expect(onSelect).toHaveBeenCalledWith('a');
  });

  it('renders rename/delete buttons that call their handlers and stop propagation', () => {
    const sessions = [session({ id: 'a', title: 'First' })];
    const onSelect = vi.fn();
    const onRename = vi.fn();
    const onDelete = vi.fn();
    render(
      <ConversationList
        {...base}
        sessions={sessions}
        onSelect={onSelect}
        onRename={onRename}
        onDelete={onDelete}
      />,
    );
    fireEvent.click(screen.getByLabelText('Rename First'));
    expect(onRename).toHaveBeenCalledWith(sessions[0]);
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText('Delete First'));
    expect(onDelete).toHaveBeenCalledWith(sessions[0]);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('marks the current session as active', () => {
    const sessions = [
      session({ id: 'a', title: 'A' }),
      session({ id: 'b', title: 'B' }),
    ];
    render(<ConversationList {...base} sessions={sessions} currentSessionId="b" />);
    const rowB = screen.getByText('B').closest('[role="button"]');
    expect(rowB?.className).toMatch(/rowActive/);
  });
});
