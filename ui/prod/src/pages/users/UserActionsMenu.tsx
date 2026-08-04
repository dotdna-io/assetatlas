// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import type { User } from '@/api/auth';
import styles from './users.module.css';

interface UserActionsMenuProps {
  user: User;
  onEdit: () => void;
  onResetPassword: () => void;
  onForcePasswordChange: () => void;
  onResetMfa: () => void;
  onSendInvite: () => void;
  onToggle: () => void;
  onDelete: () => void;
}

export function UserActionsMenu({
  user,
  onEdit,
  onResetPassword,
  onForcePasswordChange,
  onResetMfa,
  onSendInvite,
  onToggle,
  onDelete,
}: UserActionsMenuProps) {
  const [isOpen, setIsOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => setIsOpen(false), []);

  useEffect(() => {
    if (!isOpen) return;

    function handleClickOutside(e: MouseEvent) {
      if (
        menuRef.current && !menuRef.current.contains(e.target as Node) &&
        buttonRef.current && !buttonRef.current.contains(e.target as Node)
      ) {
        close();
      }
    }

    function handleEscape(e: KeyboardEvent) {
      if (e.key === 'Escape') close();
    }

    function handleScroll() {
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
  }, [isOpen, close]);

  useEffect(() => {
    if (!isOpen || !buttonRef.current || !menuRef.current) return;
    const btn = buttonRef.current.getBoundingClientRect();
    const menu = menuRef.current;
    const menuHeight = menu.offsetHeight;
    // Flip above the button when there isn't room below (bottom rows), so the
    // menu is never clipped at the viewport edge. Coordinates are viewport-based
    // and the menu is portaled to <body> (see below), so position: fixed reads
    // them correctly.
    const spaceBelow = window.innerHeight - btn.bottom;
    const openUp = spaceBelow < menuHeight + 8 && btn.top > menuHeight + 8;
    menu.style.top = openUp ? `${btn.top - menuHeight - 4}px` : `${btn.bottom + 4}px`;
    menu.style.right = `${window.innerWidth - btn.right}px`;
  }, [isOpen]);

  function handleAction(action: () => void) {
    close();
    action();
  }

  return (
    <div className={styles.actionsCell}>
      <button className={styles.iconBtn} onClick={onEdit} title="Edit user">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
        </svg>
      </button>
      <button
        ref={buttonRef}
        className={styles.iconBtn}
        onClick={() => setIsOpen(!isOpen)}
        title="More actions"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="1" />
          <circle cx="12" cy="5" r="1" />
          <circle cx="12" cy="19" r="1" />
        </svg>
      </button>
      {/* Portaled to <body>: the table sits inside a glossyCard whose
          backdrop-filter would otherwise make it the containing block for this
          position: fixed menu (breaking the viewport math) and its
          overflow: hidden would clip the menu. */}
      {isOpen && createPortal(
        <div ref={menuRef} className={styles.dropdownMenu}>
          {!user.is_ldap_user && !user.is_oidc_user && (
            <>
              <button className={styles.menuItem} onClick={() => handleAction(onResetPassword)}>
                Reset Password
              </button>
              <button className={styles.menuItem} onClick={() => handleAction(onForcePasswordChange)}>
                Force password change at next login
              </button>
              <button className={styles.menuItem} onClick={() => handleAction(onResetMfa)}>
                Reset MFA
              </button>
            </>
          )}
          {user.onboarding_pending && (
            <button className={styles.menuItem} onClick={() => handleAction(onSendInvite)}>
              Send Invite
            </button>
          )}
          <button className={styles.menuItem} onClick={() => handleAction(onToggle)}>
            {user.is_active ? 'Disable User' : 'Enable User'}
          </button>
          <button className={`${styles.menuItem} ${styles.menuItemDanger}`} onClick={() => handleAction(onDelete)}>
            Delete User
          </button>
        </div>,
        document.body,
      )}
    </div>
  );
}
