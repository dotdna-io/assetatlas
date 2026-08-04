// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { EyeIcon } from '@/components/ui/EyeIcon';
import { assetUrl } from '@/basePath';
import styles from './table.module.css';

interface RowActionsProps {
  assetId: string;
  onEdit: (id: string) => void;
}

// Shared with BulkActionsMenu, which anchors the same menu to a header cell.
export function positionMenu(menu: HTMLDivElement, anchor: HTMLElement): void {
  // Pull the menu out of flow before measuring, so its presence doesn't
  // expand the parent td and inflate rect.bottom.
  menu.style.position = 'fixed';
  const td = anchor.closest('td, th');
  const rect = (td || anchor).getBoundingClientRect();
  const menuHeight = 150;
  const flipUp = rect.bottom + menuHeight > window.innerHeight - 16;
  menu.style.left = `${rect.left}px`;
  if (flipUp) {
    menu.style.top = 'auto';
    menu.style.bottom = `${window.innerHeight - rect.top}px`;
  } else {
    menu.style.top = `${rect.bottom}px`;
    menu.style.bottom = 'auto';
  }
}

export function RowActions({ assetId, onEdit }: RowActionsProps) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;

    function handleClickOutside(e: MouseEvent) {
      if (
        menuRef.current &&
        !menuRef.current.contains(e.target as Node) &&
        btnRef.current &&
        !btnRef.current.contains(e.target as Node)
      ) {
        close();
      }
    }

    function handleEscape(e: KeyboardEvent) {
      if (e.key === 'Escape') close();
    }

    function handleScroll(e: Event) {
      if (menuRef.current && menuRef.current.contains(e.target as Node)) return;
      close();
    }
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    window.addEventListener('scroll', handleScroll, true);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
      window.removeEventListener('scroll', handleScroll, true);
    };
  }, [open, close]);

  return (
    <td className={styles.actionCell}>
      <button
        ref={btnRef}
        className={styles.actionBtn}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((prev) => !prev);
        }}
        title="Actions"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
          <circle cx="12" cy="6" r="1.5" fill="currentColor" />
          <circle cx="12" cy="12" r="1.5" fill="currentColor" />
          <circle cx="12" cy="18" r="1.5" fill="currentColor" />
        </svg>
      </button>
      {open && createPortal(
        <div
          ref={(el) => {
            menuRef.current = el;
            if (el && btnRef.current) {
              positionMenu(el, btnRef.current);
            }
          }}
          className={styles.actionMenu}
        >
          <button
            className={styles.actionMenuItem}
            onClick={() => {
              window.open(assetUrl('/asset-details?id=' + assetId), '_blank');
              close();
            }}
          >
            <EyeIcon size={17} />
            Asset Details
          </button>
          <button
            className={styles.actionMenuItem}
            onClick={() => {
              window.open(assetUrl('/asset-history?id=' + assetId), '_blank');
              close();
            }}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="17" height="17">
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            Asset History
          </button>
          <button
            className={styles.actionMenuItem}
            onClick={() => {
              onEdit(assetId);
              close();
            }}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="17" height="17">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
            </svg>
            Edit Asset
          </button>
        </div>,
        document.body,
      )}
    </td>
  );
}
