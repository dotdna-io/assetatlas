// SPDX-License-Identifier: AGPL-3.0-or-later
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DeleteIconButton } from '../DeleteIconButton';

describe('DeleteIconButton', () => {
  it('renders the canonical trash glyph inside a plain button', () => {
    const { container } = render(<DeleteIconButton title="Delete" aria-label="Delete channel" />);
    const btn = container.querySelector('button');
    expect(btn).not.toBeNull();
    expect(btn!.getAttribute('type')).toBe('button');
    expect(btn!.getAttribute('aria-label')).toBe('Delete channel');
    // The one true glyph (alert channels' trash): lid polyline + body path.
    expect(container.querySelector('polyline[points="3 6 5 6 21 6"]')).not.toBeNull();
  });

  it('honors the size prop, defaulting to 14', () => {
    const small = render(<DeleteIconButton aria-label="d" />);
    expect(small.container.querySelector('svg')!.getAttribute('width')).toBe('14');
    const large = render(<DeleteIconButton size={18} aria-label="d" />);
    expect(large.container.querySelector('svg')!.getAttribute('width')).toBe('18');
  });

  it('merges a caller className onto the button', () => {
    const { container } = render(<DeleteIconButton className="siteLayout" aria-label="d" />);
    expect(container.querySelector('button')!.className).toContain('siteLayout');
  });
});
