// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CustomHttpConfigStep } from '@/pages/integration-wizard/CustomHttpConfigStep';
import { DEFAULT_CUSTOM_HTTP_CONFIG } from '@/pages/integration-wizard/wizard-state';
import { integrationsApi } from '@/api/integrations';
vi.mock('@/api/integrations');

function renderStep(initial = DEFAULT_CUSTOM_HTTP_CONFIG, onUpdate = vi.fn()) {
  // formData.config is where the custom_http blob lives.
  const formData: any = { integration_type: 'custom_http', config: initial };
  return {
    onUpdate,
    ...render(
      <CustomHttpConfigStep
        formData={formData}
        onUpdate={onUpdate}
        mode="create"
      />,
    ),
  };
}

describe('CustomHttpConfigStep — Endpoint section', () => {
  it('renders method, URL, headers, query params controls', () => {
    renderStep();
    expect(screen.getByLabelText(/method/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/url/i)).toBeInTheDocument();
    expect(screen.getByText(/headers/i)).toBeInTheDocument();
    expect(screen.getByText(/static query params/i)).toBeInTheDocument();
  });

  it('updates url on input', () => {
    const { onUpdate } = renderStep();
    fireEvent.change(screen.getByLabelText(/url/i), {
      target: { value: 'https://api.example.com/x' },
    });
    expect(onUpdate).toHaveBeenCalled();
    const last = onUpdate.mock.calls.at(-1)![0];
    expect(last.config.url).toBe('https://api.example.com/x');
  });
});

describe('CustomHttpConfigStep — Auth section', () => {
  it('shows no auth-specific fields when auth_type=none', () => {
    renderStep();
    expect(screen.queryByLabelText(/username/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/bearer token/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^api key$/i)).not.toBeInTheDocument();
  });

  it('shows username/password when auth_type=basic', () => {
    const cfg = { ...DEFAULT_CUSTOM_HTTP_CONFIG, auth_type: 'basic' as const };
    renderStep(cfg);
    expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it('shows bearer_token when auth_type=bearer', () => {
    const cfg = { ...DEFAULT_CUSTOM_HTTP_CONFIG, auth_type: 'bearer' as const };
    renderStep(cfg);
    expect(screen.getByLabelText(/bearer token/i)).toBeInTheDocument();
  });

  it('shows api_key + location + name + prefix when auth_type=api_key', () => {
    const cfg = { ...DEFAULT_CUSTOM_HTTP_CONFIG, auth_type: 'api_key' as const };
    renderStep(cfg);
    expect(screen.getByLabelText(/^api key$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/location/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^name$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^prefix$/i)).toBeInTheDocument();
  });

  it('shows oauth2 fields when auth_type=oauth2', () => {
    const cfg = { ...DEFAULT_CUSTOM_HTTP_CONFIG, auth_type: 'oauth2' as const };
    renderStep(cfg);
    expect(screen.getByLabelText(/token url/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/client id/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/client secret/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/scope/i)).toBeInTheDocument();
  });
});

describe('CustomHttpConfigStep — Pagination section', () => {
  it('shows offset fields when pagination_type=offset', () => {
    const cfg = { ...DEFAULT_CUSTOM_HTTP_CONFIG, pagination_type: 'offset' as const };
    renderStep(cfg);
    expect(screen.getByLabelText(/page size/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/offset param/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/limit param/i)).toBeInTheDocument();
  });

  it('shows cursor fields when pagination_type=cursor', () => {
    const cfg = { ...DEFAULT_CUSTOM_HTTP_CONFIG, pagination_type: 'cursor' as const };
    renderStep(cfg);
    expect(screen.getByLabelText(/cursor request param/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/cursor response path/i)).toBeInTheDocument();
  });

  it('shows no extra fields when pagination_type=link', () => {
    const cfg = { ...DEFAULT_CUSTOM_HTTP_CONFIG, pagination_type: 'link' as const };
    renderStep(cfg);
    expect(screen.queryByLabelText(/page size/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/cursor/i)).not.toBeInTheDocument();
  });
});

describe('CustomHttpConfigStep — Body section', () => {
  it('hides body section when method=GET', () => {
    renderStep();
    expect(screen.queryByLabelText(/request body/i)).not.toBeInTheDocument();
  });

  it('shows body textarea when method=POST', () => {
    const cfg = { ...DEFAULT_CUSTOM_HTTP_CONFIG, method: 'POST' as const };
    renderStep(cfg);
    expect(screen.getByLabelText(/request body/i)).toBeInTheDocument();
  });

  it('shows inline error for invalid JSON', () => {
    const cfg = { ...DEFAULT_CUSTOM_HTTP_CONFIG, method: 'POST' as const };
    renderStep(cfg);
    fireEvent.change(screen.getByLabelText(/request body/i), {
      target: { value: '{not json' },
    });
    fireEvent.blur(screen.getByLabelText(/request body/i));
    expect(screen.getByText(/invalid json/i)).toBeInTheDocument();
  });
});

describe('CustomHttpConfigStep — Preview response button', () => {
  it('calls testCustomHttp and renders status_code on success', async () => {
    (integrationsApi.testCustomHttp as any) = vi.fn().mockResolvedValue({
      success: true, status_code: 200, elapsed_ms: 42,
      records_root_match: { valid_path: true, matched_count: 7, sample_records: [] },
      body_preview: { items: [] },
    });
    renderStep({
      ...DEFAULT_CUSTOM_HTTP_CONFIG,
      url: 'https://x',
      records_root: '$.items',
    });
    fireEvent.click(screen.getByRole('button', { name: /preview response/i }));
    expect(await screen.findByText(/200/)).toBeInTheDocument();
    expect(await screen.findByText(/7 record/i)).toBeInTheDocument();
  });

  it('renders error message on failure', async () => {
    (integrationsApi.testCustomHttp as any) = vi.fn().mockResolvedValue({
      success: false, error_type: 'auth', message: '401 Unauthorized',
    });
    renderStep({
      ...DEFAULT_CUSTOM_HTTP_CONFIG,
      url: 'https://x',
      records_root: '$.items',
    });
    fireEvent.click(screen.getByRole('button', { name: /preview response/i }));
    expect(await screen.findByText(/401 Unauthorized/)).toBeInTheDocument();
  });

  it('Preview response button is enabled without records_root being set', () => {
    renderStep({ ...DEFAULT_CUSTOM_HTTP_CONFIG, url: 'https://x', records_root: '' });
    const btn = screen.getByRole('button', { name: /^preview response$/i });
    expect(btn).toBeEnabled();
  });

  it('Preview response button is disabled when url is empty', () => {
    renderStep({ ...DEFAULT_CUSTOM_HTTP_CONFIG, url: '', records_root: '$.items' });
    const btn = screen.getByRole('button', { name: /^preview response$/i });
    expect(btn).toBeDisabled();
  });

  it('renders the raw response body in the test result', async () => {
    (integrationsApi.testCustomHttp as any) = vi.fn().mockResolvedValue({
      success: true, status_code: 200, elapsed_ms: 12,
      body_preview: { greeting: 'hello', count: 3 },
    });
    renderStep({ ...DEFAULT_CUSTOM_HTTP_CONFIG, url: 'https://x', records_root: '' });
    fireEvent.click(screen.getByRole('button', { name: /^preview response$/i }));
    // The body is JSON-stringified inside a <pre>; assert key fragments appear.
    expect(await screen.findByText(/"greeting": "hello"/)).toBeInTheDocument();
    expect(screen.getByText(/"count": 3/)).toBeInTheDocument();
  });
});

describe('CustomHttpConfigStep — Field bindings', () => {
  // FieldBindingsSection only renders when passthrough is OFF — the new
  // default is `true`, so each test below explicitly opts into bindings mode.
  const noPassthrough = { ...DEFAULT_CUSTOM_HTTP_CONFIG, passthrough: false };

  it('starts with no bindings and shows + Add binding', () => {
    renderStep(noPassthrough);
    expect(screen.getByRole('button', { name: /add binding/i })).toBeInTheDocument();
  });

  it('adds a row when + Add binding is clicked', () => {
    const { onUpdate } = renderStep(noPassthrough);
    fireEvent.click(screen.getByRole('button', { name: /add binding/i }));
    const last = onUpdate.mock.calls.at(-1)![0];
    expect(last.config.field_bindings).toHaveLength(1);
  });

  it('auto-suggests source_name from source_path leaf', () => {
    const cfg = {
      ...noPassthrough,
      field_bindings: [{ source_path: '', source_name: '' }],
    };
    const { onUpdate } = renderStep(cfg);
    const pathInputs = screen.getAllByPlaceholderText(/jsonpath/i);
    fireEvent.change(pathInputs[0], { target: { value: '$.os.name' } });
    fireEvent.blur(pathInputs[0]);
    const last = onUpdate.mock.calls.at(-1)![0];
    expect(last.config.field_bindings[0].source_name).toBe('os_name');
  });

  it('flags duplicate source_name', () => {
    const cfg = {
      ...noPassthrough,
      field_bindings: [
        { source_path: '$.a', source_name: 'x' },
        { source_path: '$.b', source_name: 'x' },
      ],
    };
    renderStep(cfg);
    expect(screen.getAllByText(/duplicate/i).length).toBeGreaterThan(0);
  });

  it('removes a row when × is clicked', () => {
    const cfg = {
      ...noPassthrough,
      field_bindings: [{ source_path: '$.a', source_name: 'a' }],
    };
    const { onUpdate } = renderStep(cfg);
    fireEvent.click(screen.getByLabelText(/remove binding 0/i));
    const last = onUpdate.mock.calls.at(-1)![0];
    expect(last.config.field_bindings).toHaveLength(0);
  });
});
