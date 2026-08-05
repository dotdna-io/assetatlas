// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AssistantMarkdown } from '../AssistantMarkdown';
import { getCategoryColor } from '@/components/ui/CategoryIcon';


describe('AssistantMarkdown · markdown rendering', () => {
  it('renders **bold** as <strong>', () => {
    render(<AssistantMarkdown content="this is **bold** text" />);
    const el = screen.getByText('bold');
    expect(el.tagName).toBe('STRONG');
  });

  it('renders bullet lists', () => {
    render(<AssistantMarkdown content={'- one\n- two\n- three'} />);
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
  });

  it('renders inline `code` as <code>', () => {
    render(<AssistantMarkdown content="run `pytest` to test" />);
    expect(screen.getByText('pytest').tagName).toBe('CODE');
  });

  it('renders fenced code blocks', () => {
    render(<AssistantMarkdown content={'```\nserver\n```'} />);
    const codeBlock = screen.getByText('server');
    expect(codeBlock.closest('pre')).not.toBeNull();
  });
});

describe('AssistantMarkdown · security', () => {
  it('does NOT execute embedded HTML script', () => {
    render(<AssistantMarkdown
      content={'plain text <script>alert(1)</script> end'} />);
    // Script tag should be dropped (not executed, not present in DOM).
    expect(document.querySelector('script')).toBeNull();
  });

  it('does NOT render embedded HTML elements (e.g. <iframe>)', () => {
    render(<AssistantMarkdown
      content={'before <iframe src="evil.com"></iframe> after'} />);
    expect(document.querySelector('iframe')).toBeNull();
  });

  it('opens external links in a new tab', () => {
    render(<AssistantMarkdown
      content="see [docs](https://example.com)" />);
    const link = screen.getByRole('link', { name: 'docs' });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel',
      expect.stringContaining('noopener'));
  });
});

/** jsdom serializes inline styles with hex colors normalized to
 *  `rgb(r, g, b)`, so convert the palette hex before comparing. */
function asRgb(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
}

/** Walk up from `el` checking whether any ancestor is styled with the
 *  shared palette color for `server` — i.e. the value was rendered as a
 *  CategoryTag. The expected color is derived from the palette (not a
 *  hardcoded literal) so a design-token change doesn't break the test. */
function hasServerBadgeAncestor(el: HTMLElement | null): boolean {
  const token = getCategoryColor('server');
  const rgb = asRgb(token);
  while (el) {
    const style = el.getAttribute('style') ?? '';
    if (style.includes(token) || style.includes(rgb)) {
      return true;
    }
    el = el.parentElement;
  }
  return false;
}

describe('AssistantMarkdown · category badges', () => {
  it('badges a category when it is the entire cell value', () => {
    render(<AssistantMarkdown content={
      '| Host | Subcategory |\n|------|-------------|\n| h1 | server |'
    } />);
    // CategoryTag renders the canonical display label ("server" → "Server").
    expect(hasServerBadgeAncestor(screen.getByText('Server'))).toBe(true);
  });

  it('does NOT badge category words that appear inside other text', () => {
    // "Server" inside the OS name "Windows Server 2012" is part of the
    // product name, not a category value — it must stay plain text.
    render(<AssistantMarkdown content={
      '| OS |\n|----|\n| Windows Server 2012 |'
    } />);
    expect(hasServerBadgeAncestor(screen.getByText('Windows Server 2012'))).toBe(false);
  });

  it('does NOT badge category words inside prose', () => {
    render(<AssistantMarkdown
      content="The host is a server in the frobnicate group" />);
    const text = screen.getByText(/the frobnicate group/i);
    expect(hasServerBadgeAncestor(text)).toBe(false);
  });

  it('does not badge category tokens inside inline code', () => {
    render(<AssistantMarkdown content="use `server` as the value" />);
    const codeEl = screen.getByText('server');
    expect(codeEl.tagName).toBe('CODE');
    expect(hasServerBadgeAncestor(codeEl)).toBe(false);
  });

  it('does not badge category tokens inside fenced code blocks', () => {
    render(<AssistantMarkdown content={'```\nserver runs nginx\n```'} />);
    const block = screen.getByText('server runs nginx');
    expect(block.closest('pre')).not.toBeNull();
    expect(hasServerBadgeAncestor(block)).toBe(false);
  });

  it('does not partial-match category tokens', () => {
    render(<AssistantMarkdown content="the server-side rendering" />);
    expect(hasServerBadgeAncestor(screen.getByText(/server-side/))).toBe(false);
  });
});
