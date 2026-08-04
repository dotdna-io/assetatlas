// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WebhookChannelForm } from '../WebhookChannelForm';
import type { AlertChannel } from '@/api/channels';

function renderForm(props: { channel?: AlertChannel } = {}) {
  const onConfigChange = vi.fn();
  render(<WebhookChannelForm onConfigChange={onConfigChange} {...props} />);
  return { onConfigChange };
}

it('emits url and omits untyped secrets for a new channel', async () => {
  const { onConfigChange } = renderForm();
  await userEvent.type(screen.getByLabelText(/url/i), 'https://hook.example/x');
  const cfg = onConfigChange.mock.calls.at(-1)![0];
  expect(cfg.url).toBe('https://hook.example/x');
  expect(cfg).not.toHaveProperty('signing_key');
  expect(cfg).not.toHaveProperty('headers');
});

it('keeps a saved secret out of the payload unless edited', async () => {
  const channel = {
    id: 'c1', type: 'webhook', name: 'ops', enabled: true,
    config: { url: 'https://x', signing_key: { configured: true } },
  };
  const { onConfigChange } = renderForm({ channel });
  await userEvent.type(screen.getByLabelText(/url/i), '/y');
  let cfg = onConfigChange.mock.calls.at(-1)![0];
  expect(cfg).not.toHaveProperty('signing_key');
  await userEvent.type(screen.getByLabelText(/signing key/i), 'newsecret');
  cfg = onConfigChange.mock.calls.at(-1)![0];
  expect(cfg.signing_key).toBe('newsecret');
});

// #918 review: WebhookChannelForm must emit ONLY on an actual field change, not
// on mount. The parent (Task 7's AlertChannelsTab) seeds its own initial config
// from defaults/the loaded channel, so the form must not assume a mount-time
// emission is needed to persist an untouched url/timeout.
describe('no mount-time emission', () => {
  it('does not call onConfigChange on render, for a new channel', () => {
    const { onConfigChange } = renderForm();
    expect(onConfigChange).not.toHaveBeenCalled();
  });

  it('does not call onConfigChange on render, for an existing channel', () => {
    const channel = {
      id: 'c1', type: 'webhook', name: 'ops', enabled: true,
      config: { url: 'https://existing.example/hook', timeout: 45, signing_key: { configured: true } },
    };
    const { onConfigChange } = renderForm({ channel });
    expect(onConfigChange).not.toHaveBeenCalled();
  });

  // Number('') is 0, which is finite — a naive `Number(text) || 30` fallback
  // would silently coerce an explicit "0" up to 30. Guard against that regression.
  it('keeps an explicit timeout of 0 instead of coercing it to the 30s default', async () => {
    const { onConfigChange } = renderForm();
    const timeoutInput = screen.getByLabelText(/timeout/i);
    await userEvent.clear(timeoutInput);
    await userEvent.type(timeoutInput, '0');
    const cfg = onConfigChange.mock.calls.at(-1)![0];
    expect(cfg.timeout).toBe(0);
  });
});

describe('headers: write-only JSON secret', () => {
  it('omits headers for a new channel until the admin types valid JSON', async () => {
    const { onConfigChange } = renderForm();
    // user-event's keyboard syntax treats "{" as a special-key marker, so a
    // literal "{" must be doubled: "{{". A standalone "}" needs no escaping.
    await userEvent.type(screen.getByLabelText(/headers/i), '{{"X-Api-Key":"abc"}');
    const cfg = onConfigChange.mock.calls.at(-1)![0];
    expect(cfg.headers).toEqual({ 'X-Api-Key': 'abc' });
  });

  it('keeps saved headers out of the payload unless edited, then emits new headers once typed', async () => {
    const channel = {
      id: 'c1', type: 'webhook', name: 'ops', enabled: true,
      config: { url: 'https://x', headers: { configured: true } },
    };
    const { onConfigChange } = renderForm({ channel });
    await userEvent.type(screen.getByLabelText(/url/i), '/y');
    let cfg = onConfigChange.mock.calls.at(-1)![0];
    expect(cfg).not.toHaveProperty('headers');
    // user-event's keyboard syntax treats "{" as a special-key marker, so a
    // literal "{" must be doubled: "{{". A standalone "}" needs no escaping.
    await userEvent.type(screen.getByLabelText(/headers/i), '{{"X-New-Header":"abc"}');
    cfg = onConfigChange.mock.calls.at(-1)![0];
    expect(cfg.headers).toEqual({ 'X-New-Header': 'abc' });
  });

  it('surfaces a validation message and does not emit invalid JSON headers', async () => {
    const { onConfigChange } = renderForm();
    await userEvent.type(screen.getByLabelText(/headers/i), '{{not json');
    expect(await screen.findByText(/invalid json/i)).toBeInTheDocument();
    const cfg = onConfigChange.mock.calls.at(-1)![0];
    expect(cfg).not.toHaveProperty('headers');
  });

  // Backend parity: WebhookChannel.validate_config 400s unless every header value
  // is a string, so a numeric value must be rejected client-side too, not emitted.
  it('rejects a non-string header value and does not emit it', async () => {
    const { onConfigChange } = renderForm();
    await userEvent.type(screen.getByLabelText(/headers/i), '{{"X-Api-Key":123}');
    expect(await screen.findByText(/flat object of string keys and values/i)).toBeInTheDocument();
    const cfg = onConfigChange.mock.calls.at(-1)![0];
    expect(cfg).not.toHaveProperty('headers');
  });
});
