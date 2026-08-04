// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { Modal } from '@/components/ui/Modal';
import { BulkRecategorizeDialog } from './BulkRecategorizeDialog';
import { BulkTagDialog } from './BulkTagDialog';
import { BulkUntagDialog } from './BulkUntagDialog';
import { positionMenu } from '../table/RowActions';
import tableStyles from '../table/table.module.css';

type BulkMode = 'recategorize' | 'clear' | 'tag' | 'untag';

const MODAL_TITLES: Record<BulkMode, string> = {
  recategorize: 'Recategorize matching assets',
  clear: 'Clear category override',
  tag: 'Tag matching assets',
  untag: 'Untag matching assets',
};

interface Props {
  filters: unknown[];
  search?: string;
  matchCount: number;
  /** Disable the trigger while the table query is in-flight: matchCount is stale
   *  relative to the live filters until the refetch lands, so the dialog would
   *  otherwise confirm a count that doesn't match the set the server will act on. */
  disabled?: boolean;
}

/** Bulk-actions trigger + dropdown. Lives in the table header cell above the
 *  per-row kebab column (these actions affect every matching row), and the
 *  dropdown reuses the row menu's look (tableStyles.actionMenu). */
export function BulkActionsMenu({ filters, search, matchCount, disabled }: Props) {
  const [mode, setMode] = useState<BulkMode | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  const closeMenu = useCallback(() => setMenuOpen(false), []);

  // Click-outside, Escape, and scroll close the dropdown (not the modal) —
  // the menu is position: fixed, so it would float away from its anchor on
  // scroll otherwise. Same pattern as RowActions.
  useEffect(() => {
    if (!menuOpen) return;
    const handleDown = (e: MouseEvent) => {
      if (menuRef.current?.contains(e.target as Node)) return;
      if (btnRef.current?.contains(e.target as Node)) return;
      closeMenu();
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeMenu();
    };
    const handleScroll = (e: Event) => {
      if (menuRef.current && menuRef.current.contains(e.target as Node)) return;
      closeMenu();
    };
    document.addEventListener('mousedown', handleDown);
    document.addEventListener('keydown', handleKey);
    window.addEventListener('scroll', handleScroll, true);
    return () => {
      document.removeEventListener('mousedown', handleDown);
      document.removeEventListener('keydown', handleKey);
      window.removeEventListener('scroll', handleScroll, true);
    };
  }, [menuOpen, closeMenu]);

  // A switch rather than a ternary chain: four modes, and two of them share the
  // same dialog.
  const renderDialog = () => {
    switch (mode) {
      case 'tag':
        return (
          <BulkTagDialog
            filters={filters}
            search={search}
            matchCount={matchCount}
            onClose={() => setMode(null)}
          />
        );
      case 'untag':
        return (
          <BulkUntagDialog
            filters={filters}
            search={search}
            matchCount={matchCount}
            onClose={() => setMode(null)}
          />
        );
      case 'recategorize':
      case 'clear':
        return (
          <BulkRecategorizeDialog
            mode={mode}
            filters={filters}
            search={search}
            matchCount={matchCount}
            onClose={() => setMode(null)}
          />
        );
      default:
        return null;
    }
  };

  return (
    <>
      <button
        ref={btnRef}
        className={tableStyles.actionBtn}
        disabled={matchCount === 0 || disabled}
        onClick={() => setMenuOpen((o) => !o)}
        title="Bulk actions"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
      >
        {/* List-with-bullets icon */}
        <svg
          viewBox="0 0 24 24"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <line x1="8" y1="6" x2="21" y2="6" />
          <line x1="8" y1="12" x2="21" y2="12" />
          <line x1="8" y1="18" x2="21" y2="18" />
          <line x1="3" y1="6" x2="3.01" y2="6" />
          <line x1="3" y1="12" x2="3.01" y2="12" />
          <line x1="3" y1="18" x2="3.01" y2="18" />
        </svg>
      </button>
      {menuOpen &&
        createPortal(
          <div
            ref={(el) => {
              menuRef.current = el;
              if (el && btnRef.current) {
                positionMenu(el, btnRef.current);
              }
            }}
            role="menu"
            className={tableStyles.actionMenu}
          >
            <button
              role="menuitem"
              className={tableStyles.actionMenuItem}
              onClick={() => {
                setMode('recategorize');
                closeMenu();
              }}
            >
              {/* Tag with a green dot — assigns a category */}
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="17" height="17" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
                <circle cx="7" cy="7" r="1.9" fill="var(--success)" stroke="none" />
              </svg>
              Recategorize matching
            </button>
            <button
              role="menuitem"
              className={tableStyles.actionMenuItem}
              onClick={() => {
                setMode('clear');
                closeMenu();
              }}
            >
              {/* Same tag with a red dot — clears the override */}
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="17" height="17" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
                <circle cx="7" cy="7" r="1.9" fill="var(--error)" stroke="none" />
              </svg>
              Clear category override
            </button>
            <button
              role="menuitem"
              className={tableStyles.actionMenuItem}
              onClick={() => {
                setMode('tag');
                closeMenu();
              }}
            >
              {/* Same tag with a blue dot — adds a manual tag */}
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="17" height="17" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
                <circle cx="7" cy="7" r="1.9" fill="var(--accent)" stroke="none" />
              </svg>
              Tag matching
            </button>
            <button
              role="menuitem"
              className={tableStyles.actionMenuItem}
              onClick={() => {
                setMode('untag');
                closeMenu();
              }}
            >
              {/* Same tag with a red dot — removes a manual tag */}
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="17" height="17" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
                <circle cx="7" cy="7" r="1.9" fill="var(--error)" stroke="none" />
              </svg>
              Untag matching
            </button>
          </div>,
          document.body,
        )}

      {/* Dialog modal — rendered at body level via Modal's createPortal */}
      <Modal
        isOpen={mode !== null}
        onClose={() => setMode(null)}
        title={mode !== null ? MODAL_TITLES[mode] : ''}
        size="md"
        showCloseButton
      >
        {renderDialog()}
      </Modal>
    </>
  );
}
