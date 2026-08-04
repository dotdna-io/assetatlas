// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/Button';
import { useAuth } from '@/auth/useAuth';
import { useUpdateProfileSelf } from '@/api/auth';
import { getApiErrorMessage } from '@/api/client';
import styles from './AccountPage.module.css';

export function ProfileSection() {
  const { user, refreshUser } = useAuth();
  const update = useUpdateProfileSelf();
  const [fullName, setFullName] = useState(user?.full_name ?? '');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  useEffect(() => {
    setFullName(user?.full_name ?? '');
  }, [user?.full_name]);

  const dirty = fullName !== (user?.full_name ?? '');

  async function handleSave() {
    setError('');
    setSuccess('');
    try {
      await update.mutateAsync({ full_name: fullName });
      await refreshUser();
      setSuccess('Profile updated.');
    } catch (e) {
      setError(getApiErrorMessage(e, 'Update failed'));
    }
  }

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionHeading}>Profile</h2>
      <div className={styles.formGroup}>
        <label htmlFor="account-username" className={styles.label}>Username</label>
        <input
          id="account-username"
          type="text"
          value={user?.username ?? ''}
          disabled
          className={styles.input}
        />
      </div>
      <div className={styles.formGroup}>
        <label htmlFor="account-email" className={styles.label}>Email</label>
        <input
          id="account-email"
          type="email"
          value={user?.email ?? ''}
          disabled
          className={styles.input}
        />
      </div>
      <div className={styles.formGroup}>
        <label htmlFor="account-fullname" className={styles.label}>Display name</label>
        <input
          id="account-fullname"
          type="text"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          maxLength={200}
          className={styles.input}
        />
      </div>
      <div className={styles.actions}>
        <Button onClick={handleSave} disabled={!dirty || update.isPending}>
          {update.isPending ? 'Saving…' : 'Save changes'}
        </Button>
      </div>
      {error && <div className={styles.error}>{error}</div>}
      {success && <div className={styles.success}>{success}</div>}
    </section>
  );
}
