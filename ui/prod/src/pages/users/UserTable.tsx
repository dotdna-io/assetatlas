// SPDX-License-Identifier: AGPL-3.0-or-later
import type { User } from '@/api/auth';
import { UserActionsMenu } from './UserActionsMenu';
import { getInitials, getAvatarColor, formatRelativeTime } from './user-utils';
import styles from './users.module.css';

interface UserTableProps {
  users: User[];
  isLoading: boolean;
  page: number;
  limit: number;
  total: number;
  onPageChange: (page: number) => void;
  onEdit: (user: User) => void;
  onResetPassword: (user: User) => void;
  onForcePasswordChange: (user: User) => void;
  onResetMfa: (user: User) => void;
  onSendInvite: (user: User) => void;
  onToggle: (user: User) => void;
  onDelete: (user: User) => void;
}

// Role shown as plain colored text (no badge). Colours are theme-aware via
// the .role* classes in users.module.css.
const ROLE_TEXT: Record<string, { cls: string; label: string }> = {
  super_admin: { cls: styles.roleSuperAdmin, label: 'Super Admin' },
  admin:       { cls: styles.roleAdmin, label: 'Admin' },
  user:        { cls: styles.roleUser, label: 'User' },
};

// Where the account authenticates. Directory-backed sign-ins (LDAP / SSO) read
// blue like other linked-identity accents; a plain local account stays neutral.
function authTypeFor(user: User): { cls: string; label: string } {
  if (user.is_ldap_user) return { cls: styles.authDirectory, label: 'LDAP' };
  if (user.is_oidc_user) return { cls: styles.authDirectory, label: 'SSO' };
  return { cls: styles.authLocal, label: 'Local' };
}

export function UserTable({
  users,
  isLoading,
  page,
  limit,
  total,
  onPageChange,
  onEdit,
  onResetPassword,
  onForcePasswordChange,
  onResetMfa,
  onSendInvite,
  onToggle,
  onDelete,
}: UserTableProps) {
  const totalPages = Math.ceil(total / limit);
  const start = (page - 1) * limit + 1;
  const end = Math.min(page * limit, total);

  function buildPageNumbers(): number[] {
    const pages: number[] = [];
    const maxVisible = 5;
    let startPage = Math.max(1, page - Math.floor(maxVisible / 2));
    const endPage = Math.min(totalPages, startPage + maxVisible - 1);
    if (endPage - startPage < maxVisible - 1) {
      startPage = Math.max(1, endPage - maxVisible + 1);
    }
    for (let i = startPage; i <= endPage; i++) pages.push(i);
    return pages;
  }

  return (
    <div className={styles.tableCard}>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <colgroup>
            <col style={{ width: '20%' }} />
            <col style={{ width: '20%' }} />
            <col style={{ width: '12%' }} />
            <col style={{ width: '11%' }} />
            <col style={{ width: '10%' }} />
            <col style={{ width: '14%' }} />
            <col style={{ width: '13%' }} />
          </colgroup>
          <thead>
            <tr>
              <th className={styles.th}>User</th>
              <th className={styles.th}>Email</th>
              <th className={styles.th}>Role</th>
              <th className={styles.th}>Auth Type</th>
              <th className={styles.th}>Status</th>
              <th className={styles.th}>Last Active</th>
              <th className={`${styles.th} ${styles.thRight}`}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={7} className={styles.emptyCell}>Loading...</td>
              </tr>
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={7} className={styles.emptyCell}>No users found</td>
              </tr>
            ) : (
              users.map((user) => {
                const roleCfg = ROLE_TEXT[user.role] ?? ROLE_TEXT.user;
                const authCfg = authTypeFor(user);
                return (
                  <tr key={user.id} className={!user.is_active ? styles.rowInactive : ''}>
                    <td className={styles.td}>
                      <div className={styles.userCell}>
                        <div
                          className={styles.avatar}
                          style={{ backgroundColor: getAvatarColor(user) }}
                        >
                          {getInitials(user)}
                        </div>
                        <div>
                          <div className={styles.username}>{user.username}</div>
                          {user.full_name && (
                            <div className={styles.fullName}>{user.full_name}</div>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className={styles.td}>
                      <span className={styles.email}>{user.email}</span>
                    </td>
                    <td className={styles.td}>
                      <span className={`${styles.roleText} ${roleCfg.cls}`}>
                        {roleCfg.label}
                      </span>
                    </td>
                    <td className={styles.td}>
                      <span className={`${styles.authType} ${authCfg.cls}`}>
                        {authCfg.label}
                      </span>
                    </td>
                    <td className={styles.td}>
                      <span className={user.is_active ? styles.statSuccess : styles.statDanger}>
                        {user.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td className={styles.td}>
                      <span className={styles.lastActive}>
                        {formatRelativeTime(user.last_login_at)}
                      </span>
                    </td>
                    <td className={styles.td}>
                      <UserActionsMenu
                        user={user}
                        onEdit={() => onEdit(user)}
                        onResetPassword={() => onResetPassword(user)}
                        onForcePasswordChange={() => onForcePasswordChange(user)}
                        onResetMfa={() => onResetMfa(user)}
                        onSendInvite={() => onSendInvite(user)}
                        onToggle={() => onToggle(user)}
                        onDelete={() => onDelete(user)}
                      />
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {total > 0 && (
        <div className={styles.pagination}>
          <span className={styles.pageInfo}>
            Showing {start}–{end} of {total} users
          </span>
          <div className={styles.pageButtons}>
            <button
              className={styles.pageBtn}
              disabled={page <= 1}
              onClick={() => onPageChange(page - 1)}
            >
              Prev
            </button>
            {buildPageNumbers().map((p) => (
              <button
                key={p}
                className={`${styles.pageBtn} ${p === page ? styles.pageBtnActive : ''}`}
                onClick={() => onPageChange(p)}
              >
                {p}
              </button>
            ))}
            <button
              className={styles.pageBtn}
              disabled={page >= totalPages}
              onClick={() => onPageChange(page + 1)}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
