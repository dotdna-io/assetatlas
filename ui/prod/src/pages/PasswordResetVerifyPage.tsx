// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, FormEvent } from 'react';
import { Link, Navigate, useSearchParams, useNavigate } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import { assetUrl } from '@/basePath';
import { useDarkMode, useDocumentTitle } from '@/hooks';
import { usePasswordResetVerify } from '@/api/auth';
import { PasswordStrengthMeter, getRequirements } from '@/components/shared/PasswordStrengthMeter';
import { EyeIcon } from '@/components/ui/EyeIcon';
import styles from './password-reset.module.css';

export default function PasswordResetVerifyPage() {
  useDocumentTitle('Password Reset');
  const { isAuthenticated } = useAuth();
  const { isDarkMode, toggle: toggleDarkMode } = useDarkMode();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') ?? '';

  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const [countdown, setCountdown] = useState(3);

  const mutation = usePasswordResetVerify({
    onSuccess: () => setSuccess(true),
    onError: (err) => {
      const msg = err.message || 'Password reset failed';
      if (msg.includes('expired') || msg.includes('invalid') || msg.includes('Invalid')) {
        setError('This reset link has expired or is invalid. Please request a new one.');
      } else {
        setError(msg);
      }
    },
  });

  // Countdown after success
  useEffect(() => {
    if (!success) return;
    if (countdown <= 0) {
      navigate('/login', { replace: true });
      return;
    }
    const timer = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [success, countdown, navigate]);

  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  // No token in URL
  if (!token) {
    return (
      <div className={styles.page}>
        <div className={styles.container}>
          <div className={styles.card}>
            <div className={styles.logo}>
              <img src={assetUrl('/assetatlas_logo.png')} alt="AssetAtlas" />
            </div>
            <div className={styles.error}>
              Invalid reset link. Please request a new password reset.
            </div>
            <div className={styles.backLink}>
              <Link to="/password-reset">Request New Link</Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');

    if (!password) {
      setError('Please enter a new password');
      return;
    }

    const reqs = getRequirements(password);
    if (reqs.some((r) => !r.met)) {
      setError('Password does not meet all requirements');
      return;
    }

    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    mutation.mutate({ token, new_password: password });
  }

  const passwordsMatch = confirmPassword.length > 0 && password === confirmPassword;
  const passwordsMismatch = confirmPassword.length > 0 && password !== confirmPassword;

  return (
    <div className={styles.page}>
      <button
        className={styles.themeToggle}
        onClick={toggleDarkMode}
        title="Toggle dark mode"
        type="button"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          {isDarkMode ? (
            <>
              <circle cx="12" cy="12" r="5" />
              <line x1="12" y1="1" x2="12" y2="3" /><line x1="12" y1="21" x2="12" y2="23" />
              <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" /><line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
              <line x1="1" y1="12" x2="3" y2="12" /><line x1="21" y1="12" x2="23" y2="12" />
              <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" /><line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
            </>
          ) : (
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
          )}
        </svg>
      </button>

      <div className={styles.container}>
        <div className={styles.card}>
          <div className={styles.logo}>
            <img src={assetUrl('/assetatlas_logo.png')} alt="AssetAtlas" />
          </div>
          <p className={styles.subtitle}>
            {success ? 'Password reset' : 'Choose a new password'}
          </p>

          {success ? (
            <div className={styles.successState}>
              <div className={styles.successIcon}>
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              </div>
              <h3 className={styles.successTitle}>Password updated!</h3>
              <p className={styles.successText}>
                Your password has been reset successfully.
              </p>
              <p className={styles.countdown}>
                Redirecting to login in <strong>{countdown}s</strong>...
              </p>
              <div className={styles.loginLink}>
                <Link to="/login">Go to Login now</Link>
              </div>
            </div>
          ) : (
            <>
              {error && <div className={styles.error}>{error}</div>}

              <form onSubmit={handleSubmit}>
                <div className={styles.formGroup}>
                  <label className={styles.label} htmlFor="new-password">New password</label>
                  <div className={styles.passwordWrapper}>
                    <input
                      id="new-password"
                      type={showPassword ? 'text' : 'password'}
                      className={styles.input}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="Minimum 8 characters"
                      autoComplete="new-password"
                      autoFocus
                      disabled={mutation.isPending}
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
                  <PasswordStrengthMeter password={password} />
                </div>

                <div className={styles.formGroup}>
                  <label className={styles.label} htmlFor="confirm-password">Confirm password</label>
                  <input
                    id="confirm-password"
                    type="password"
                    className={`${styles.input} ${passwordsMatch ? styles.inputSuccess : ''} ${passwordsMismatch ? styles.inputError : ''}`}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="Re-enter your password"
                    autoComplete="new-password"
                    disabled={mutation.isPending}
                  />
                  {passwordsMatch && (
                    <span className={styles.fieldSuccess}>
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                      Passwords match
                    </span>
                  )}
                  {passwordsMismatch && (
                    <span className={styles.fieldError}>Passwords do not match</span>
                  )}
                </div>

                <button
                  type="submit"
                  className={styles.submitBtn}
                  disabled={mutation.isPending || !password || !confirmPassword}
                >
                  {mutation.isPending ? (
                    <span className={styles.spinner} />
                  ) : (
                    'Reset Password'
                  )}
                </button>
              </form>
            </>
          )}

          {!success && (
            <div className={styles.backLink}>
              <Link to="/login">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="19" y1="12" x2="5" y2="12" /><polyline points="12 19 5 12 12 5" />
                </svg>
                Back to Login
              </Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
