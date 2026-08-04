// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useRef, useState, FormEvent } from 'react';
import { useNavigate, useSearchParams, Link, Navigate } from 'react-router-dom';
import { QRCode } from 'react-qrcode-logo';
import { useAuth } from '@/auth/useAuth';
import { useDocumentTitle } from '@/hooks';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { Button } from '@/components/ui/Button';
import pageStyles from './OidcCompletePage.module.css';
import mfaStyles from './profile/MfaEnrollment.module.css';
import localStyles from './MfaEnrollmentRequiredPage.module.css';

/**
 * Drives the post-login MFA enrollment flow when a local user is forced to enroll
 * because the user is local and has no MFA enrolled. The user arrives with an
 * `?token=...` query string handed off by `LoginPage`.
 *
 *   /login         → 200 { requires_mfa_enrollment, mfa_enrollment_token }
 *   ↓
 *   /mfa-enrollment-required?token=<...>
 *      ↓ POST /mfa/enroll-setup → secret + provisioning URI (QR code)
 *      ↓ user scans QR + enters code
 *      ↓ POST /mfa/enroll-verify → access + refresh tokens
 */
export default function MfaEnrollmentRequiredPage() {
  useDocumentTitle('Set up two-factor authentication');
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { mfaEnrollSetup, mfaEnrollVerify, isAuthenticated } = useAuth();

  const enrollmentToken = params.get('token') || '';

  const [setupData, setSetupData] = useState<{
    secret: string;
    provisioning_uri: string;
  } | null>(null);
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isVerifying, setIsVerifying] = useState(false);

  // Strict-mode double-mount guard for the setup call.
  const setupStartedRef = useRef(false);

  useEffect(() => {
    if (!enrollmentToken) {
      setError('Missing enrollment token. Please log in again.');
      return;
    }
    if (setupStartedRef.current) return;
    setupStartedRef.current = true;

    mfaEnrollSetup(enrollmentToken)
      .then(setSetupData)
      .catch((err: unknown) => {
        setError(
          err instanceof Error ? err.message : 'Failed to start MFA setup',
        );
      });
  }, [enrollmentToken, mfaEnrollSetup]);

  // Already authenticated AND no enrollment in progress → user has nothing
  // to do here (e.g. direct navigation to this URL after sign-in). Send to
  // dashboard.
  if (isAuthenticated && !enrollmentToken) {
    return <Navigate to="/dashboard" replace />;
  }

  async function handleVerify(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!code.trim()) {
      setError('Enter the 6-digit code from your authenticator app');
      return;
    }
    setIsVerifying(true);
    try {
      await mfaEnrollVerify(enrollmentToken, code.trim());
      navigate('/dashboard', { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Verification failed');
      setIsVerifying(false);
    }
  }

  if (error && !setupData) {
    return (
      <div className={pageStyles.page}>
        <div className={pageStyles.container}>
          <div className={pageStyles.card}>
            <div className={pageStyles.error}>{error}</div>
            <Link to="/login" className={pageStyles.link}>
              Back to sign-in
            </Link>
          </div>
        </div>
      </div>
    );
  }

  if (!setupData) {
    return (
      <div className={pageStyles.page}>
        <div className={pageStyles.container}>
          <div className={pageStyles.card}>
            <LoadingSpinner />
            <p>Preparing two-factor enrollment…</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={pageStyles.page}>
      <div className={pageStyles.container}>
        <div className={pageStyles.card}>
          <h2>Two-factor authentication required</h2>
          <p className={mfaStyles.description}>
            Your administrator requires two-factor authentication for admin
            accounts. Scan the QR code below with an authenticator app
            (Google Authenticator, Authy, 1Password, etc.) and enter the
            6-digit code to complete sign-in.
          </p>

          <div className={`${mfaStyles.qrBlock} ${localStyles.qrBlock}`}>
            <QRCode
              value={setupData.provisioning_uri}
              size={208}
              ecLevel="M"
              qrStyle="dots"
              eyeRadius={8}
              fgColor="#7CB342"
              eyeColor="#374151"
              bgColor="transparent"
              quietZone={12}
            />
            <div className={`${mfaStyles.secretRow} ${localStyles.secretRow}`}>
              <span className={mfaStyles.secretLabel}>
                Manual entry secret:
              </span>
              <code className={mfaStyles.secret}>{setupData.secret}</code>
            </div>
          </div>

          <form onSubmit={handleVerify}>
            <div className={`${mfaStyles.formGroup} ${localStyles.formGroup}`}>
              <label className={mfaStyles.label} htmlFor="code">
                6-digit code
              </label>
              <input
                id="code"
                type="text"
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="123456"
                autoFocus
                required
                disabled={isVerifying}
                className={mfaStyles.codeInput}
              />
            </div>
            <div className={`${mfaStyles.actions} ${localStyles.actions}`}>
              <Button type="submit" disabled={isVerifying}>
                {isVerifying ? 'Verifying…' : 'Verify and sign in'}
              </Button>
            </div>
          </form>

          {error && <div className={mfaStyles.error}>{error}</div>}
        </div>
      </div>
    </div>
  );
}
