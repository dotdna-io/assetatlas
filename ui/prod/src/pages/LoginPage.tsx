// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, FormEvent } from 'react';
import { useNavigate, Navigate } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import { useDocumentTitle } from '@/hooks';
import { assetUrl } from '@/basePath';
import { EyeIcon } from '@/components/ui/EyeIcon';
import { useSsoProviders, useStartOidcLogin } from '@/api/auth';
import { getApiErrorMessage } from '@/api/client';
import { SsoProviderIcon } from '@/components/ui/SsoProviderIcon';
import styles from './LoginPage.module.css';

type Step = 'credentials' | 'mfa';

export default function LoginPage() {
  useDocumentTitle('Login');
  const navigate = useNavigate();
  const { login, verifyMfaChallenge, isAuthenticated } = useAuth();
  const ssoProviders = useSsoProviders();
  const startOidc = useStartOidcLogin();

  const [step, setStep] = useState<Step>('credentials');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [mfaChallengeToken, setMfaChallengeToken] = useState('');
  const [mfaCode, setMfaCode] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  // If already authenticated, redirect to dashboard
  if (isAuthenticated) {
    const intended = localStorage.getItem('intended_path') || '/dashboard';
    return <Navigate to={intended} replace />;
  }

  function navigateAfterLogin() {
    const intended = localStorage.getItem('intended_path') || '/dashboard';
    localStorage.removeItem('intended_path');
    navigate(intended, { replace: true });
  }

  async function handleOidcClick() {
    setError('');
    try {
      const result = await startOidc.mutateAsync();
      window.location.href = result.authorization_url;
    } catch (e) {
      setError(getApiErrorMessage(e, 'SSO sign-in failed'));
    }
  }

  async function handleCredentials(e: FormEvent) {
    e.preventDefault();
    setError('');

    if (!username.trim() || !password) {
      setError('Please enter both username and password');
      return;
    }

    setIsLoading(true);
    try {
      const result = await login(username.trim(), password);
      if (result.kind === 'mfa_required') {
        setMfaChallengeToken(result.mfaChallengeToken);
        setStep('mfa');
        setIsLoading(false);
      } else if (result.kind === 'mfa_enrollment_required') {
        // Admin must enroll MFA before getting tokens. Hand the
        // single-use enrollment token to the dedicated page.
        navigate(
          `/mfa-enrollment-required?token=${encodeURIComponent(result.mfaEnrollmentToken)}`,
          { replace: true },
        );
      } else {
        navigateAfterLogin();
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Login failed';
      if (msg.includes('Invalid credentials') || msg.includes('Invalid username or password')) {
        setError('Invalid username or password');
      } else if (msg.includes('Account locked')) {
        setError('Your account has been locked. Please contact an administrator.');
      } else if (msg.includes('timeout')) {
        setError('Request timeout. Please try again.');
      } else {
        setError(msg);
      }
      setIsLoading(false);
    }
  }

  async function handleMfa(e: FormEvent) {
    e.preventDefault();
    setError('');

    if (!mfaCode.trim()) {
      setError('Enter the 6-digit code from your authenticator app');
      return;
    }

    setIsLoading(true);
    try {
      await verifyMfaChallenge(mfaChallengeToken, mfaCode.trim());
      navigateAfterLogin();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'MFA verification failed';
      if (msg.includes('expired') || msg.includes('challenge')) {
        setError('Your sign-in session expired. Please start over.');
        setStep('credentials');
        setMfaChallengeToken('');
        setMfaCode('');
        setPassword('');
      } else {
        setError(msg.includes('match') ? 'Code did not match. Try again.' : msg);
      }
      setIsLoading(false);
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.container}>
        <div className={styles.card}>
          <div className={styles.logo}>
            <img src={assetUrl('/assetatlas_logo_full.png')} alt="AssetAtlas" />
          </div>

          {error && <div className={styles.error}>{error}</div>}

          {step === 'credentials' && ssoProviders.data?.oidc.enabled && (
            <>
              <button
                type="button"
                className={styles.ssoBtn}
                onClick={handleOidcClick}
                disabled={startOidc.isPending}
              >
                <SsoProviderIcon kind={ssoProviders.data.oidc.provider_kind} />
                <span>
                  {startOidc.isPending
                    ? 'Redirecting…'
                    : `Sign in with ${ssoProviders.data.oidc.provider_name}`}
                </span>
              </button>
              <div className={styles.ssoDivider}>or</div>
            </>
          )}

          {step === 'credentials' && (
            <form onSubmit={handleCredentials}>
              <div className={styles.formGroup}>
                <label className={styles.label} htmlFor="username">Username</label>
                <input
                  id="username"
                  type="text"
                  className={styles.input}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="Enter your username"
                  autoComplete="username"
                  required
                  disabled={isLoading}
                />
              </div>

              <div className={styles.formGroup}>
                <label className={styles.label} htmlFor="password">Password</label>
                <div className={styles.passwordWrapper}>
                  <input
                    id="password"
                    type={showPassword ? 'text' : 'password'}
                    className={styles.input}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Enter your password"
                    autoComplete="current-password"
                    required
                    disabled={isLoading}
                  />
                  <button
                    type="button"
                    className={styles.passwordToggle}
                    onClick={() => setShowPassword((v) => !v)}
                    title={showPassword ? 'Hide password' : 'Show password'}
                  >
                    <EyeIcon size={20} open={!showPassword} />
                  </button>
                </div>
              </div>

              <div className={styles.forgotPassword}>
                <a href="/password-reset">Forgot password?</a>
              </div>

              <button
                type="submit"
                className={styles.submitBtn}
                disabled={isLoading}
              >
                {isLoading ? (
                  <span className={styles.spinner} />
                ) : (
                  'Sign In'
                )}
              </button>
            </form>
          )}

          {step === 'mfa' && (
            <form onSubmit={handleMfa}>
              <div className={styles.formGroup}>
                <label className={styles.label} htmlFor="mfaCode">
                  Authenticator code
                </label>
                <input
                  id="mfaCode"
                  type="text"
                  inputMode="numeric"
                  className={styles.codeInput}
                  value={mfaCode}
                  onChange={(e) => setMfaCode(e.target.value)}
                  placeholder="123 456"
                  autoComplete="one-time-code"
                  autoFocus
                  required
                  disabled={isLoading}
                />
              </div>

              <button type="submit" className={styles.submitBtn} disabled={isLoading}>
                {isLoading ? <span className={styles.spinner} /> : 'Verify'}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
