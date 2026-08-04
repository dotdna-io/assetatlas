// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { FormEvent } from 'react';
import { Button } from '@/components/ui/Button';
import { PasswordStrengthMeter } from '@/components/shared/PasswordStrengthMeter';
import { useAuth } from '@/auth/useAuth';
import { useChangePasswordSelf } from '@/api/auth';
import { getApiErrorMessage } from '@/api/client';
import styles from './AccountPage.module.css';

interface Props {
  forcePasswordChange: boolean;
}

export function PasswordSection({ forcePasswordChange }: Props) {
  const { refreshUser } = useAuth();
  const navigate = useNavigate();
  const change = useChangePasswordSelf();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setSuccess('');
    if (next !== confirm) {
      setError("New passwords don't match.");
      return;
    }
    try {
      await change.mutateAsync({ current_password: current, new_password: next });
      setSuccess('Password changed.');
      setCurrent('');
      setNext('');
      setConfirm('');
      await refreshUser();
      if (forcePasswordChange) {
        navigate('/account', { replace: true });
      }
    } catch (err) {
      setError(getApiErrorMessage(err, 'Password change failed'));
    }
  }

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionHeading}>Password</h2>
      {forcePasswordChange && (
        <div className={styles.banner}>
          Your password has expired — please change it to continue.
        </div>
      )}
      <p className={styles.sectionDescription}>
        Your password is stored hashed with bcrypt. After a successful change, all
        active sessions for this account remain valid; sign out other devices manually
        if needed.
      </p>
      <form onSubmit={handleSubmit}>
        <div className={styles.formGroup}>
          <label htmlFor="acct-current-pw" className={styles.label}>Current password</label>
          <input
            id="acct-current-pw"
            type="password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            autoComplete="current-password"
            required
            className={styles.input}
          />
        </div>
        <div className={styles.formGroup}>
          <label htmlFor="acct-new-pw" className={styles.label}>New password</label>
          <input
            id="acct-new-pw"
            type="password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            autoComplete="new-password"
            required
            minLength={8}
            maxLength={128}
            className={styles.input}
          />
          {next && <PasswordStrengthMeter password={next} />}
        </div>
        <div className={styles.formGroup}>
          <label htmlFor="acct-confirm-pw" className={styles.label}>Confirm new password</label>
          <input
            id="acct-confirm-pw"
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            required
            minLength={8}
            maxLength={128}
            className={styles.input}
          />
        </div>
        <div className={styles.actions}>
          <Button type="submit" disabled={change.isPending || !current || !next || !confirm}>
            {change.isPending ? 'Saving…' : 'Change password'}
          </Button>
        </div>
      </form>
      {error && <div className={styles.error}>{error}</div>}
      {success && <div className={styles.success}>{success}</div>}
    </section>
  );
}
