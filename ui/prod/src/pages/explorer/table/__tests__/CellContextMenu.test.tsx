// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { CellContextMenu } from '../CellContextMenu';

describe('CellContextMenu', () => {
  it('renders an item as two lines: the action label and the search detail', () => {
    render(
      <CellContextMenu
        x={10}
        y={20}
        items={[{ label: 'Add to search', detail: 'Category = Workstation', onSelect: vi.fn() }]}
        onClose={vi.fn()}
      />,
    );
    const item = screen.getByRole('menuitem');
    expect(item).toHaveTextContent('Add to search');
    expect(item).toHaveTextContent('Category = Workstation');
  });

  it('calls the item onSelect then onClose when an item is clicked', () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <CellContextMenu
        x={0}
        y={0}
        items={[{ label: 'Add to search', onSelect }]}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Add to search' }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes when Escape is pressed', () => {
    const onClose = vi.fn();
    render(
      <CellContextMenu
        x={0}
        y={0}
        items={[{ label: 'x', onSelect: vi.fn() }]}
        onClose={onClose}
      />,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes when a mousedown lands outside the menu', () => {
    const onClose = vi.fn();
    render(
      <div>
        <button>outside</button>
        <CellContextMenu
          x={0}
          y={0}
          items={[{ label: 'x', onSelect: vi.fn() }]}
          onClose={onClose}
        />
      </div>,
    );
    fireEvent.mouseDown(screen.getByText('outside'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
