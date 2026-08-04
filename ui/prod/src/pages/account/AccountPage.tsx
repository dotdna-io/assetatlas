// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useDocumentTitle } from '@/hooks';
import { useAuth } from '@/auth/useAuth';
import { ProfileSection } from './ProfileSection';
import { PasswordSection } from './PasswordSection';
import { MfaSection } from './MfaSection';
import styles from './AccountPage.module.css';

export default function AccountPage() {
  useDocumentTitle('Account');
  const { user } = useAuth();
  const [params] = useSearchParams();
  const force = params.get('force');
  const passwordSectionRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (force === 'password' && passwordSectionRef.current) {
      passwordSectionRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [force]);

  const isLocal = !user?.is_ldap_user && !user?.is_oidc_user;

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <h1 className={styles.title}>Account</h1>
        <p className={styles.subtitle}>{user?.email}</p>
      </header>

      <ProfileSection />

      {isLocal && (
        <div ref={passwordSectionRef}>
          <PasswordSection forcePasswordChange={user?.must_change_password === true} />
        </div>
      )}

      <MfaSection />
    </div>
  );
}
