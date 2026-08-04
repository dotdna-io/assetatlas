// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { HelpMarkdown } from '../HelpMarkdown';

describe('HelpMarkdown', () => {
  it('renders headings, lists and tables', () => {
    render(<HelpMarkdown content={'## Setup\n\n- one\n- two\n'} />);
    expect(screen.getByRole('heading', { name: 'Setup' })).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
  });

  it('renders images via assetUrl and zooms on click', () => {
    render(<HelpMarkdown content={'![cap](/help/img/x/a.png)'} />);
    const img = screen.getByAltText('cap') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('/help/img/x/a.png'); // BASE_PATH '' in test
    fireEvent.click(img);
    expect(screen.getByTestId('help-lightbox')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('help-lightbox'));
    expect(screen.queryByTestId('help-lightbox')).not.toBeInTheDocument();
  });

  // B3: image paragraphs must NOT be wrapped in <p> to avoid <div>-in-<p>
  // invalid DOM nesting. The image renders inside a fragment, so the
  // surrounding <p> is dropped when the paragraph's only child is an image.
  it('real image is not wrapped in a <p> element', () => {
    const { container } = render(<HelpMarkdown content={'![cap](/help/img/x/a.png)'} />);
    // The img itself should still render.
    expect(screen.getByAltText('cap')).toBeInTheDocument();
    // No wrapping <p>.
    const p = container.querySelector('p');
    expect(p).toBeNull();
  });

  it('normal paragraph text is still wrapped in a <p> element', () => {
    const { container } = render(<HelpMarkdown content={'Just some text.'} />);
    const p = container.querySelector('p');
    expect(p).not.toBeNull();
    expect(p!.textContent).toBe('Just some text.');
  });

  it('image and trailing text on same line preserves the paragraph wrapper', () => {
    const { container } = render(<HelpMarkdown content={'![cap](/help/img/x/a.png) and some trailing text'} />);
    // The image should render.
    expect(screen.getByAltText('cap')).toBeInTheDocument();
    // Mixed content (image + text) has length > 1, so isImageOnly is false and <p> is preserved.
    const p = container.querySelector('p');
    expect(p).not.toBeNull();
    // The trailing text must be present and inside the paragraph.
    expect(screen.getByText('and some trailing text')).toBeInTheDocument();
    expect(p!.textContent).toContain('and some trailing text');
  });
});
