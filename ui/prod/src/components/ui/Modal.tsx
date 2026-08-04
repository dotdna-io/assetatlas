// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useRef, useCallback } from 'react';
import type { ReactNode, MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import styles from './Modal.module.css';

type ModalSize = 'sm' | 'md' | 'lg' | 'xxl' | 'xl';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  size?: ModalSize;
  closeOnEscape?: boolean;
  closeOnBackdrop?: boolean;
  showCloseButton?: boolean;
  titleAlign?: 'left' | 'center';
}

export function Modal({
  isOpen,
  onClose,
  title,
  children,
  size = 'md',
  closeOnEscape = true,
  // Off by default: modals hold form state, and a stray click outside must
  // not discard it. Modals that genuinely want click-outside-to-close opt in.
  closeOnBackdrop = false,
  showCloseButton = false,
  titleAlign = 'left',
}: ModalProps) {
  const modalRef = useRef<HTMLDivElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape' && closeOnEscape) {
        onClose();
        return;
      }

      // Focus trap
      if (e.key === 'Tab' && modalRef.current) {
        const focusable = modalRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (focusable.length === 0) return;

        const first = focusable[0];
        const last = focusable[focusable.length - 1];

        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    },
    [onClose, closeOnEscape]
  );

  // Stable ref for the keydown handler so the effect doesn't re-run on every render
  const handleKeyDownRef = useRef(handleKeyDown);
  handleKeyDownRef.current = handleKeyDown;

  useEffect(() => {
    if (!isOpen) return;

    previousFocusRef.current = document.activeElement as HTMLElement;
    const handler = (e: KeyboardEvent) => handleKeyDownRef.current(e);
    document.addEventListener('keydown', handler);
    document.body.style.overflow = 'hidden';

    // Focus the modal itself on open (only once)
    requestAnimationFrame(() => {
      modalRef.current?.focus();
    });

    return () => {
      document.removeEventListener('keydown', handler);
      document.body.style.overflow = '';
      previousFocusRef.current?.focus();
    };
  }, [isOpen]);

  // Only treat a backdrop click as a close if the press also started on the
  // backdrop. A drag that starts inside the modal (text selection in an input)
  // and releases outside dispatches the click on the overlay — that must not
  // discard the user's in-progress form state.
  const mouseDownOnBackdropRef = useRef(false);

  const handleBackdropMouseDown = (e: MouseEvent) => {
    mouseDownOnBackdropRef.current = e.target === e.currentTarget;
  };

  const handleBackdropClick = (e: MouseEvent) => {
    if (closeOnBackdrop && e.target === e.currentTarget && mouseDownOnBackdropRef.current) {
      onClose();
    }
    mouseDownOnBackdropRef.current = false;
  };

  if (!isOpen) return null;

  return createPortal(
    <div className={styles.overlay} onMouseDown={handleBackdropMouseDown} onClick={handleBackdropClick}>
      <div
        ref={modalRef}
        className={`${styles.modal} ${styles[size]}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        tabIndex={-1}
      >
        <div className={`${styles.header} ${titleAlign === 'center' ? styles.headerCentered : ''}`}>
          <h2 id="modal-title" className={`${styles.title} ${titleAlign === 'center' ? styles.titleCentered : ''}`}>
            {title}
          </h2>
          {showCloseButton && (
            <button
              className={styles.closeBtn}
              onClick={onClose}
              aria-label="Close"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
            </button>
          )}
        </div>
        <div className={styles.body}>{children}</div>
      </div>
    </div>,
    document.body
  );
}
