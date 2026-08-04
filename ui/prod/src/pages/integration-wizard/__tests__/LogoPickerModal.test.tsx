// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { LogoPickerModal } from '../LogoPickerModal';
import { apiClient, ApiError } from '@/api/client';

// The Modal component renders via portal; jsdom needs a body to mount into.
// jsdom provides one by default, no extra setup required.

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

const LIBRARY_RESPONSE = {
  success: true,
  logos: [
    { filename: 'jamf.png', path: '/logos/jamf.png', source: 'library' as const },
    {
      filename: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png',
      path: '/api/integrations/logos/file/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png',
      source: 'uploaded' as const,
    },
  ],
};

function renderModal(props: Partial<React.ComponentProps<typeof LogoPickerModal>> = {}) {
  const onClose = vi.fn();
  const onSelect = vi.fn();
  const qc = makeQueryClient();
  // Pre-populate the cache so the component renders synchronously.
  qc.setQueryData(['integrations', 'logos'], LIBRARY_RESPONSE);

  render(
    <QueryClientProvider client={qc}>
      <LogoPickerModal
        isOpen
        onClose={onClose}
        onSelect={onSelect}
        selectedLogo={null}
        {...props}
      />
    </QueryClientProvider>
  );

  return { onClose, onSelect, qc };
}

describe('LogoPickerModal', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders both Library and Upload tabs', () => {
    renderModal();
    expect(screen.getByRole('button', { name: 'Library' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Upload custom' })).toBeInTheDocument();
  });

  it('shows the library grid with bundled and uploaded logos labelled correctly', () => {
    renderModal();
    // Bundled
    expect(screen.getByAltText('Jamf')).toBeInTheDocument();
    // Uploaded item shows the "Custom · ……" formatted name
    expect(screen.getByText(/Custom · /)).toBeInTheDocument();
    // …and an explicit "Custom" badge
    expect(screen.getByText('Custom')).toBeInTheDocument();
  });

  it('selecting a library logo passes the served path to onSelect', () => {
    const { onSelect, onClose } = renderModal();
    fireEvent.click(screen.getByAltText('Jamf'));
    expect(onSelect).toHaveBeenCalledWith('/logos/jamf.png');
    expect(onClose).toHaveBeenCalled();
  });

  it('selecting an uploaded logo passes the API path to onSelect', () => {
    const { onSelect } = renderModal();
    const customBtn = screen.getByText(/Custom · /).closest('button')!;
    fireEvent.click(customBtn);
    expect(onSelect).toHaveBeenCalledWith(
      expect.stringMatching(/^\/api\/integrations\/logos\/file\//),
    );
  });

  it('switching to the Upload tab reveals the drop zone', () => {
    renderModal();
    fireEvent.click(screen.getByRole('button', { name: 'Upload custom' }));
    expect(
      screen.getByText(/Drop a logo here or click to browse/i),
    ).toBeInTheDocument();
  });

  it('rejects oversized files client-side and shows an error', () => {
    renderModal();
    fireEvent.click(screen.getByRole('button', { name: 'Upload custom' }));

    const input = screen.getByLabelText(/Upload custom logo/i) as HTMLInputElement;
    // 2 MB blob — over the 1 MB cap.
    const big = new File([new Uint8Array(2 * 1024 * 1024)], 'big.png', {
      type: 'image/png',
    });
    fireEvent.change(input, { target: { files: [big] } });

    expect(screen.getByRole('alert').textContent).toMatch(/too large|Limit is 1 MB/);
  });

  it('rejects unsupported extensions client-side', () => {
    renderModal();
    fireEvent.click(screen.getByRole('button', { name: 'Upload custom' }));
    const input = screen.getByLabelText(/Upload custom logo/i) as HTMLInputElement;
    const exe = new File([new Uint8Array(10)], 'evil.exe', {
      type: 'application/octet-stream',
    });
    fireEvent.change(input, { target: { files: [exe] } });
    expect(screen.getByRole('alert').textContent).toMatch(/Unsupported|Allowed/);
  });

  it('uploads a valid file and calls onSelect with the returned path', async () => {
    // Stub apiClient.upload to return a synthetic uploaded-logo response.
    const sha = 'b'.repeat(64);
    const uploadSpy = vi.spyOn(apiClient, 'upload').mockResolvedValue({
      success: true,
      logo: {
        filename: `${sha}.png`,
        path: `/api/integrations/logos/file/${sha}.png`,
        source: 'uploaded',
      },
    });

    const { onSelect, onClose } = renderModal();
    fireEvent.click(screen.getByRole('button', { name: 'Upload custom' }));

    const input = screen.getByLabelText(/Upload custom logo/i) as HTMLInputElement;
    const png = new File([new Uint8Array(1024)], 'logo.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [png] } });

    fireEvent.click(screen.getByRole('button', { name: /Upload & use/i }));

    await waitFor(() => {
      expect(uploadSpy).toHaveBeenCalledWith(
        '/integrations/logos/upload',
        expect.any(FormData),
      );
      expect(onSelect).toHaveBeenCalledWith(
        `/api/integrations/logos/file/${sha}.png`,
      );
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('surfaces server-side validation errors from the API', async () => {
    vi.spyOn(apiClient, 'upload').mockRejectedValue(
      new ApiError(
        'Request failed: POST /integrations/logos/upload (400)',
        400,
        { message: 'File is not a valid PNG/JPEG/WEBP image.' },
      ),
    );

    renderModal();
    fireEvent.click(screen.getByRole('button', { name: 'Upload custom' }));

    const input = screen.getByLabelText(/Upload custom logo/i) as HTMLInputElement;
    const png = new File([new Uint8Array(100)], 'logo.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [png] } });
    fireEvent.click(screen.getByRole('button', { name: /Upload & use/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(
        /not a valid PNG|JPEG/,
      );
    });
  });
});
