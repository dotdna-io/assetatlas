// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';

import * as chat from '../chat';
import { apiClient } from '../client';

vi.mock('../client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    del: vi.fn(),
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
});

describe('chat api', () => {
  it('createSession POSTs /chat/sessions', async () => {
    (apiClient.post as any).mockResolvedValue({
      session_id: 's1',
      title: 't',
      created_at: '2026-04-22T00:00:00Z',
    });
    const out = await chat.createSession('t');
    expect(apiClient.post).toHaveBeenCalledWith('/chat/sessions', { title: 't' });
    expect(out.session_id).toBe('s1');
  });

  it('createSession omits title when not provided (backend applies default)',
     async () => {
    (apiClient.post as any).mockResolvedValue({
      session_id: 's1',
      title: 'Untitled conversation',
      created_at: '2026-04-22T00:00:00Z',
    });
    await chat.createSession();
    expect(apiClient.post).toHaveBeenCalledWith('/chat/sessions', {});
  });

  it('listSessions GETs with pagination params', async () => {
    (apiClient.get as any).mockResolvedValue({
      sessions: [], total: 0, page: 2, per_page: 10,
    });
    await chat.listSessions({ page: 2, perPage: 10 });
    expect(apiClient.get).toHaveBeenCalledWith(
      '/chat/sessions',
      { params: { page: 2, per_page: 10 } },
    );
  });

  it('listSessions uses defaults when no opts given', async () => {
    (apiClient.get as any).mockResolvedValue({
      sessions: [], total: 0, page: 1, per_page: 20,
    });
    await chat.listSessions();
    expect(apiClient.get).toHaveBeenCalledWith(
      '/chat/sessions',
      { params: { page: 1, per_page: 20 } },
    );
  });

  it('getSession GETs /chat/sessions/:id', async () => {
    (apiClient.get as any).mockResolvedValue({
      session: { id: 's1', title: 't', model: 'claude-opus-4-7',
                 provider: 'anthropic',
                 created_at: 'x', updated_at: 'y' },
      messages: [],
    });
    const out = await chat.getSession('s1');
    expect(apiClient.get).toHaveBeenCalledWith('/chat/sessions/s1');
    expect(out.session.id).toBe('s1');
  });

  it('renameSession PATCHes /chat/sessions/:id', async () => {
    (apiClient.patch as any).mockResolvedValue({
      id: 's1', title: 'new',
      model: 'x', provider: 'y',
      created_at: 'a', updated_at: 'b',
    });
    await chat.renameSession('s1', 'new');
    expect(apiClient.patch).toHaveBeenCalledWith(
      '/chat/sessions/s1',
      { title: 'new' },
    );
  });

  it('deleteSession DELETEs /chat/sessions/:id', async () => {
    (apiClient.del as any).mockResolvedValue(undefined);
    await chat.deleteSession('s1');
    expect(apiClient.del).toHaveBeenCalledWith('/chat/sessions/s1');
  });

  it('sendFeedback POSTs to /chat/feedback', async () => {
    (apiClient.post as any).mockResolvedValue(undefined);
    await chat.sendFeedback('m1', 1, 'great');
    expect(apiClient.post).toHaveBeenCalledWith(
      '/chat/feedback',
      { message_id: 'm1', rating: 1, comment: 'great' },
    );
  });

  it('sendFeedback omits comment when not provided', async () => {
    (apiClient.post as any).mockResolvedValue(undefined);
    await chat.sendFeedback('m1', -1);
    expect(apiClient.post).toHaveBeenCalledWith(
      '/chat/feedback',
      { message_id: 'm1', rating: -1 },
    );
  });

});
