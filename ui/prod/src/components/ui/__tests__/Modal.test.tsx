// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Modal } from '../Modal';

function renderModal(props: Partial<React.ComponentProps<typeof Modal>> = {}) {
  const onClose = vi.fn();
  render(
    <Modal isOpen onClose={onClose} title="Test modal" {...props}>
      <input aria-label="Name" />
    </Modal>
  );
  // The overlay is the dialog's direct parent (portal root child).
  const overlay = screen.getByRole('dialog').parentElement!;
  return { onClose, overlay };
}

describe('Modal backdrop close', () => {
  it('does not close on backdrop click by default — modals hold form state, a stray click must not discard it', () => {
    const { onClose, overlay } = renderModal();

    fireEvent.mouseDown(overlay);
    fireEvent.click(overlay);

    expect(onClose).not.toHaveBeenCalled();
  });

  it('closes on a genuine backdrop click when closeOnBackdrop is opted in', () => {
    const { onClose, overlay } = renderModal({ closeOnBackdrop: true });

    fireEvent.mouseDown(overlay);
    fireEvent.click(overlay);

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not close when a drag starts inside the modal and releases on the backdrop, even with closeOnBackdrop', () => {
    // Selecting text in an input and overshooting the modal edge makes the
    // browser dispatch the click on the overlay (common ancestor of mousedown
    // and mouseup targets). That must NOT count as a backdrop click — it was
    // silently discarding in-progress form state (e.g. the enrichment CSV
    // upload modal).
    const { onClose, overlay } = renderModal({ closeOnBackdrop: true });

    fireEvent.mouseDown(screen.getByLabelText('Name'));
    fireEvent.click(overlay);

    expect(onClose).not.toHaveBeenCalled();
  });

  it('still closes on Escape by default — a deliberate keypress, unlike a stray click', () => {
    const { onClose } = renderModal();

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
