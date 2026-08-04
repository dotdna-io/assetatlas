// SPDX-License-Identifier: AGPL-3.0-or-later
import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react';
import styles from './CellContextMenu.module.css';

export interface CellContextMenuItem {
  /** Primary action line. */
  label: string;
  /** Optional secondary line — e.g. the search expression being added. */
  detail?: string;
  /** Optional leading icon (e.g. an SVG glyph). */
  icon?: ReactNode;
  onSelect: () => void;
}

interface CellContextMenuProps {
  /** Viewport-relative cursor coordinates where the menu should open. */
  x: number;
  y: number;
  items: CellContextMenuItem[];
  onClose: () => void;
}

/**
 * A small right-click popover anchored at the cursor. Closes on item select,
 * outside mousedown, Escape, or scroll. Clamps itself inside the viewport once
 * measured. Styling mirrors the column-filter dropdown.
 */
export function CellContextMenu({ x, y, items, onClose }: CellContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: x, top: y });

  // Keep the menu inside the viewport once its real size is known.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    const margin = 8;
    let left = x;
    let top = y;
    if (width && left + width > window.innerWidth - margin) {
      left = Math.max(margin, window.innerWidth - width - margin);
    }
    if (height && top + height > window.innerHeight - margin) {
      top = Math.max(margin, window.innerHeight - height - margin);
    }
    setPos({ left, top });
  }, [x, y]);

  useEffect(() => {
    function handleMouseDown(e: MouseEvent) {
      if (ref.current && ref.current.contains(e.target as Node)) return;
      onClose();
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    function handleScroll() {
      onClose();
    }
    document.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('keydown', handleKeyDown);
    window.addEventListener('scroll', handleScroll, true);
    return () => {
      document.removeEventListener('mousedown', handleMouseDown);
      document.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('scroll', handleScroll, true);
    };
  }, [onClose]);

  return (
    <div
      ref={ref}
      className={styles.menu}
      role="menu"
      style={{ left: pos.left, top: pos.top }}
      onContextMenu={(e) => e.preventDefault()}
    >
      {items.map((item, i) => (
        <button
          key={i}
          type="button"
          role="menuitem"
          className={styles.item}
          onClick={() => {
            item.onSelect();
            onClose();
          }}
        >
          {item.icon && <span className={styles.icon}>{item.icon}</span>}
          <span className={styles.text}>
            <span className={styles.label}>{item.label}</span>
            {item.detail && <span className={styles.detail}>{item.detail}</span>}
          </span>
        </button>
      ))}
    </div>
  );
}
