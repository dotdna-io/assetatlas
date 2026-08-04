// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useRef } from 'react';
import type { SavedFilter } from '@/api/saved-filters';
import { DeleteIconButton } from '@/components/ui/IconButton';
import styles from './saved-filters.module.css';

export interface SavedFiltersMenuProps {
  isOpen: boolean;
  onClose: () => void;
  filters: SavedFilter[];
  library: SavedFilter[];
  loading: boolean;
  error?: boolean;
  onSaveCurrent: () => void;
  onLoad: (f: SavedFilter) => void;
  onShare: (f: SavedFilter) => void;
  onRename: (f: SavedFilter) => void;
  onDelete: (f: SavedFilter) => void;
  triggerRef?: React.RefObject<HTMLElement | null>;
}

export function SavedFiltersMenu({
  isOpen, onClose, filters, library, loading, error,
  onSaveCurrent, onLoad, onShare, onRename, onDelete, triggerRef,
}: SavedFiltersMenuProps) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  useEffect(() => {
    if (!isOpen) return;
    const onMouseDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (panelRef.current && panelRef.current.contains(t)) return;
      if (triggerRef?.current && triggerRef.current.contains(t)) return;
      onClose();
    };
    const timer = setTimeout(() => document.addEventListener('mousedown', onMouseDown), 0);
    return () => { clearTimeout(timer); document.removeEventListener('mousedown', onMouseDown); };
  }, [isOpen, onClose, triggerRef]);

  if (!isOpen) return null;

  return (
    <div ref={panelRef} className={styles.menu}>
      <button className={styles.menuAction} onClick={onSaveCurrent}>+ Save current filter…</button>
      <div className={styles.divider} />
      <div className={styles.sectionLabel}>My filters</div>
      {loading && <div className={styles.empty}>Loading…</div>}
      {error && <div className={styles.empty}>Couldn&rsquo;t load saved filters</div>}
      {!loading && !error && filters.length === 0 && <div className={styles.empty}>No saved filters yet</div>}
      {filters.map((f) => (
        <div key={f.id} className={styles.row}>
          <button className={styles.rowName} title={f.description || f.name} onClick={() => onLoad(f)}>
            {f.name}
          </button>
          <button className={styles.rowBtn} title="Copy share link" onClick={() => onShare(f)} aria-label={`Share ${f.name}`}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          </button>
          <button className={styles.rowBtn} title="Edit" onClick={() => onRename(f)} aria-label={`Edit ${f.name}`}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          </button>
          <DeleteIconButton className={styles.rowBtn} title="Delete" onClick={() => onDelete(f)} aria-label={`Delete ${f.name}`} />
        </div>
      ))}
      {library.length > 0 && (
        <>
          <div className={styles.divider} />
          <div className={styles.sectionLabel}>Library</div>
          {library.map((f) => (
            <div key={f.id} className={styles.row}>
              <button className={styles.rowName} title={f.description || f.name} onClick={() => onLoad(f)}>
                {f.name}
              </button>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
