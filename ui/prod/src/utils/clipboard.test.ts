// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { downloadBlob } from './clipboard';

describe('downloadBlob', () => {
  beforeEach(() => {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true, value: vi.fn(() => 'blob:mock'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true, value: vi.fn(),
    });
  });

  it('creates an object URL for the blob and revokes it', () => {
    const click = vi.fn();
    vi.spyOn(document, 'createElement').mockReturnValue(
      { href: '', download: '', click } as unknown as HTMLAnchorElement,
    );
    const blob = new Blob(['x'], { type: 'text/csv' });
    downloadBlob(blob, 'out.csv');
    expect(URL.createObjectURL).toHaveBeenCalledWith(blob);
    expect(click).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock');
  });
});
