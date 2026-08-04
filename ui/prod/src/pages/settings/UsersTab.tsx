// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import {
  useUsers,
  useUserStats,
  useCreateUser,
  useUpdateUser,
  useToggleUser,
  useDeleteUser,
  useResendInvite,
  useAdminPasswordReset,
  useForcePasswordChange,
  useResetMfa,
} from '@/api/auth';
import type { User, CreateUserRequest, UpdateUserRequest } from '@/api/auth';
import { UserStatsCards } from '../users/UserStatsCards';
import { UserFilters } from '../users/UserFilters';
import { UserTable } from '../users/UserTable';
import { UserModal } from '../users/UserModal';
import { InviteCreatedDialog } from '../users/InviteCreatedDialog';
import { Button } from '@/components/ui/Button';
import styles from '../users/users.module.css';

export function UsersTab() {
  const [statusMsg, setStatusMsg] = useInlineStatus();
  const [searchParams, setSearchParams] = useSearchParams();

  // URL-synced filters
  const search = searchParams.get('search') ?? '';
  const role = searchParams.get('role') ?? '';
  const status = searchParams.get('status') ?? '';
  const page = parseInt(searchParams.get('page') ?? '1', 10);
  const limit = 10;

  // Build API params
  const apiParams = {
    page,
    limit,
    ...(search && { search }),
    ...(role && { role }),
    ...(status && { is_active: status === 'true' }),
  };

  const { data: usersData, isLoading: usersLoading } = useUsers(apiParams);
  const { data: statsData, isLoading: statsLoading } = useUserStats();

  // Modal state
  const [modalState, setModalState] = useState<{
    type:
      | 'create'
      | 'edit'
      | 'delete'
      | 'toggle'
      | 'reset-password'
      | 'force-password-change'
      | 'reset-mfa'
      | null;
    user: User | null;
  }>({ type: null, user: null });

  function closeModal() {
    setModalState({ type: null, user: null });
  }

  // Post-create invite dialog state
  const [inviteResult, setInviteResult] = useState<{
    username: string;
    email: string;
    inviteUrl: string;
    emailSent: boolean;
    expiresAt: string | null;
  } | null>(null);

  // URL param helpers
  const setParam = useCallback((key: string, value: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key !== 'page') next.delete('page'); // reset page on filter change
      return next;
    });
  }, [setSearchParams]);

  // Mutations
  const createMutation = useCreateUser({
    onSuccess: (response) => {
      closeModal();
      if (response.invite_url) {
        setInviteResult({
          username: response.user.username,
          email: response.user.email,
          inviteUrl: response.invite_url,
          emailSent: response.email_sent,
          expiresAt: response.invite_expires_at,
        });
      } else {
        setStatusMsg(null); // the new user appears in the table
      }
    },
    onError: (err) => setStatusMsg({ kind: 'error', message: err.message }),
  });

  const updateMutation = useUpdateUser(modalState.user?.id ?? '', {
    onSuccess: () => {
      setStatusMsg(null); // the row shows the change
      closeModal();
    },
    onError: (err) => setStatusMsg({ kind: 'error', message: err.message }),
  });

  const toggleMutation = useToggleUser({
    onSuccess: (_, vars) => {
      setStatusMsg(null); // the row's status badge flips
      closeModal();
    },
    onError: (err) => setStatusMsg({ kind: 'error', message: err.message }),
  });

  const deleteMutation = useDeleteUser({
    onSuccess: () => {
      closeModal();
    },
    onError: (err) => setStatusMsg({ kind: 'error', message: err.message }),
  });

  // Admin "Reset Password" — uses the dedicated admin endpoint so the
  // audit trail records user_password_changed_by_admin (vs the public
  // /password-reset/request which emits password_reset_request).
  const adminResetPwMutation = useAdminPasswordReset({
    onSuccess: () => {
      setStatusMsg({ kind: 'success', message: 'Password reset email sent' });
      closeModal();
    },
    onError: (err) => setStatusMsg({ kind: 'error', message: err.message }),
  });

  const forcePasswordChangeMutation = useForcePasswordChange({
    onSuccess: () => {
      setStatusMsg({ kind: 'success', message: 'User will be required to change their password on next login.' });
      closeModal();
    },
    onError: (err) => setStatusMsg({ kind: 'error', message: err.message }),
  });

  const resetMfaMutation = useResetMfa({
    onSuccess: () => {
      setStatusMsg({ kind: 'success', message: 'MFA reset. The user will re-enroll on next login.' });
      closeModal();
    },
    onError: (err) => setStatusMsg({ kind: 'error', message: err.message }),
  });

  const resendInvite = useResendInvite();

  function handleSendInvite(user: User) {
    resendInvite.mutate(user.id, {
      onSuccess: (response) => {
        setInviteResult({
          username: user.username,
          email: user.email,
          inviteUrl: response.invite_url,
          emailSent: response.email_sent,
          expiresAt: response.invite_expires_at,
        });
      },
      onError: (err: Error) => {
        setStatusMsg({ kind: 'error', message: err.message || 'Failed to resend invite' });
      },
    });
  }

  function handleModalSubmit(data: CreateUserRequest | UpdateUserRequest) {
    if (modalState.type === 'create') {
      createMutation.mutate(data as CreateUserRequest);
    } else if (modalState.type === 'edit' && modalState.user) {
      updateMutation.mutate(data as UpdateUserRequest);
    }
  }

  return (
    <div className={styles.page}>
      <InlineStatus status={statusMsg} />
      <div className={styles.statsHeaderRow}>
        <UserStatsCards stats={statsData} isLoading={statsLoading} />
        <Button
          variant="primary"
          onClick={() => setModalState({ type: 'create', user: null })}
        >
          Create User
        </Button>
      </div>

      <UserFilters
        search={search}
        role={role}
        status={status}
        onSearchChange={(v) => setParam('search', v)}
        onRoleChange={(v) => setParam('role', v)}
        onStatusChange={(v) => setParam('status', v)}
      />

      <UserTable
        users={usersData?.users ?? []}
        isLoading={usersLoading}
        page={page}
        limit={limit}
        total={usersData?.total ?? 0}
        onPageChange={(p) => setParam('page', String(p))}
        onEdit={(u) => setModalState({ type: 'edit', user: u })}
        onResetPassword={(u) => setModalState({ type: 'reset-password', user: u })}
        onForcePasswordChange={(u) => setModalState({ type: 'force-password-change', user: u })}
        onResetMfa={(u) => setModalState({ type: 'reset-mfa', user: u })}
        onSendInvite={(u) => handleSendInvite(u)}
        onToggle={(u) => {
          if (u.is_active) {
            setModalState({ type: 'toggle', user: u });
          } else {
            toggleMutation.mutate({ id: u.id, enable: true });
          }
        }}
        onDelete={(u) => setModalState({ type: 'delete', user: u })}
      />

      {/* Create / Edit Modal */}
      <UserModal
        isOpen={modalState.type === 'create' || modalState.type === 'edit'}
        onClose={closeModal}
        mode={modalState.type === 'edit' ? 'edit' : 'create'}
        user={modalState.user}
        onSubmit={handleModalSubmit}
        isSubmitting={createMutation.isPending || updateMutation.isPending}
      />

      {/* Delete Confirmation */}
      <ConfirmDialog
        isOpen={modalState.type === 'delete'}
        onCancel={closeModal}
        onConfirm={() => {
          if (modalState.user) deleteMutation.mutate(modalState.user.id);
        }}
        title="Delete User"
        message={`Are you sure you want to permanently delete ${modalState.user?.username ?? ''}${modalState.user?.full_name ? ` (${modalState.user.full_name})` : ''}? This will revoke all active sessions and cannot be undone.`}
        confirmLabel="Delete User"
        variant="danger"
      />

      {/* Disable Confirmation */}
      <ConfirmDialog
        isOpen={modalState.type === 'toggle'}
        onCancel={closeModal}
        onConfirm={() => {
          if (modalState.user) toggleMutation.mutate({ id: modalState.user.id, enable: false });
        }}
        title="Disable User"
        message={`Disable ${modalState.user?.username ?? ''}${modalState.user?.full_name ? ` (${modalState.user.full_name})` : ''}? They will be logged out immediately and unable to sign in until re-enabled.`}
        confirmLabel="Disable User"
        variant="warning"
      />

      {/* Reset Password Confirmation */}
      <ConfirmDialog
        isOpen={modalState.type === 'reset-password'}
        onCancel={closeModal}
        onConfirm={() => {
          if (modalState.user) adminResetPwMutation.mutate(modalState.user.id);
        }}
        title="Reset Password"
        message={`Send a password reset email to ${modalState.user?.username ?? ''}${modalState.user?.full_name ? ` (${modalState.user.full_name})` : ''}? They will receive a link to set a new password.`}
        confirmLabel="Send Reset Email"
        variant="warning"
      />

      {/* Force password change at next login */}
      <ConfirmDialog
        isOpen={modalState.type === 'force-password-change'}
        onCancel={closeModal}
        onConfirm={() => {
          if (modalState.user) forcePasswordChangeMutation.mutate(modalState.user.id);
        }}
        title="Force Password Change"
        message={`Require ${modalState.user?.username ?? ''}${modalState.user?.full_name ? ` (${modalState.user.full_name})` : ''} to change their password on next login? Their current password keeps working until then.`}
        confirmLabel="Force Change"
        variant="warning"
      />

      {/* Reset MFA */}
      <ConfirmDialog
        isOpen={modalState.type === 'reset-mfa'}
        onCancel={closeModal}
        onConfirm={() => {
          if (modalState.user) resetMfaMutation.mutate(modalState.user.id);
        }}
        title="Reset MFA"
        message={`Reset MFA for ${modalState.user?.username ?? ''}${modalState.user?.full_name ? ` (${modalState.user.full_name})` : ''}? Their current authenticator will stop working and they will be re-enrolled on next login.`}
        confirmLabel="Reset MFA"
        variant="warning"
      />

      {/* Invite Created Dialog */}
      {inviteResult && (
        <InviteCreatedDialog
          isOpen={inviteResult !== null}
          onClose={() => setInviteResult(null)}
          username={inviteResult.username}
          email={inviteResult.email}
          inviteUrl={inviteResult.inviteUrl}
          emailSent={inviteResult.emailSent}
          expiresAt={inviteResult.expiresAt}
        />
      )}
    </div>
  );
}
