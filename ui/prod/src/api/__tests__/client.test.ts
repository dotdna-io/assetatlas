// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { apiClient, ApiError } from '../client';

describe('ApiClient error handling', () => {
  beforeEach(() => {
    localStorage.setItem('access_token', 'test-token');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('surfaces a non-JSON error response without "body stream already read"', async () => {
    // Reproduces the enrichment-toggle 405: Flask short-circuits with a non-JSON
    // Werkzeug error page. The old handler read the one-shot Response body twice
    // (`.json()` then `.text()`), throwing a TypeError that masked the real 405.
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('<!doctype html><title>405 Method Not Allowed</title>', {
        status: 405,
        statusText: 'Method Not Allowed',
        headers: { 'content-type': 'text/html' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    let caught: unknown;
    try {
      await apiClient.put('/enrichment/datasets/ds-1', { status: 'inactive' });
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    const err = caught as ApiError;
    expect(err.status).toBe(405);
    expect(err.message).toContain('Request failed: PUT /enrichment/datasets/ds-1 (405)');
    // The bug surfaced as this TypeError in place of the ApiError above:
    expect(err.message).not.toContain('body stream already read');
    // A non-JSON body is preserved as raw text on the error.
    expect(err.body).toContain('Method Not Allowed');
  });

  it('parses a JSON error body onto ApiError.body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ success: false, error: 'nope' }), {
        status: 400,
        headers: { 'content-type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    let caught: unknown;
    try {
      await apiClient.get('/whatever');
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).body).toMatchObject({ error: 'nope' });
  });
});
