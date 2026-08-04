// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams, Link, Navigate } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import { useDocumentTitle } from '@/hooks';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import styles from './OidcCompletePage.module.css';

const ERROR_MESSAGES: Record<string, string> = {
  oidc_failed: 'Single sign-on failed. Please try again.',
  oidc_invalid_state: 'Sign-in session expired. Please start over.',
  oidc_no_email: 'Sign-in succeeded but no email was returned by the identity provider.',
  account_disabled: 'This account has been disabled.',
  oidc_user_not_provisioned:
    'Your account has not been provisioned. Please contact your administrator.',
};

export default function OidcCompletePage() {
  useDocumentTitle('Completing sign-in…');
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { exchangeOidcCode, isAuthenticated } = useAuth();

  const [error, setError] = useState<string | null>(null);
  const exchangedRef = useRef(false);

  const code = params.get('code');
  const errorParam = params.get('error');

  // Already authenticated and no pending exchange — go straight to dashboard.
  if (isAuthenticated && !errorParam && !code) {
    return <Navigate to="/dashboard" replace />;
  }

  useEffect(() => {
    if (errorParam) {
      setError(ERROR_MESSAGES[errorParam] ?? 'Sign-in failed.');
      return;
    }
    if (!code) {
      setError('Missing sign-in code.');
      return;
    }
    // Guard against React StrictMode double-mount — exchange code is single-use.
    if (exchangedRef.current) return;
    exchangedRef.current = true;

    exchangeOidcCode(code)
      .then(() => {
        navigate('/dashboard', { replace: true });
      })
      .catch((err: any) => {
        if (err?.code === 'invalid_or_expired_code') {
          setError('Sign-in session expired. Please start over.');
        } else {
          setError(err instanceof Error ? err.message : 'Sign-in failed.');
        }
      });
  }, [code, errorParam, exchangeOidcCode, navigate]);

  if (error) {
    return (
      <div className={styles.page}>
        <div className={styles.container}>
          <div className={styles.card}>
            <div className={styles.error}>{error}</div>
            <Link to="/login" className={styles.link}>
              Back to sign-in
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.container}>
        <div className={styles.card}>
          <LoadingSpinner />
          <p>Completing sign-in…</p>
        </div>
      </div>
    </div>
  );
}
