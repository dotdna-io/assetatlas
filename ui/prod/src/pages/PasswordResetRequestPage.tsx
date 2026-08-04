// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import { assetUrl } from '@/basePath';
import { useDarkMode, useDocumentTitle } from '@/hooks';
import {
  usePasswordResetRequest,
  useForgotPasswordTotp,
} from '@/api/auth';
import { getApiErrorMessage } from '@/api/client';
import { PasswordStrengthMeter } from '@/components/shared/PasswordStrengthMeter';
import styles from './password-reset.module.css';

type Step = 'username' | 'choose' | 'email' | 'totp' | 'submitted';

export default function PasswordResetRequestPage() {
  useDocumentTitle('Password Reset');
  const { isAuthenticated } = useAuth();
  const { isDarkMode, toggle: toggleDarkMode } = useDarkMode();

  const [step, setStep] = useState<Step>('username');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');

  const emailMutation = usePasswordResetRequest({
    onSuccess: () => setStep('submitted'),
    onError: () => setStep('submitted'),
  });
  const totpMutation = useForgotPasswordTotp({
    onSuccess: () => setStep('submitted'),
  });

  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  function handleUsernameContinue(e: FormEvent) {
    e.preventDefault();
    setError('');
    if (!username.trim()) {
      setError('Please enter your username.');
      return;
    }
    setStep('choose');
  }

  function handleEmailSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setError('Please enter a valid email address.');
      return;
    }
    emailMutation.mutate(email.trim());
  }

  async function handleTotpSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    if (newPassword !== confirm) {
      setError("New passwords don't match.");
      return;
    }
    try {
      await totpMutation.mutateAsync({
        username: username.trim(),
        code: code.trim(),
        new_password: newPassword,
      });
    } catch (err) {
      setError(
        getApiErrorMessage(
          err,
          'Could not reset your password with that code. Try again or use email reset.',
        ),
      );
    }
  }

  return (
    <div className={styles.page}>
      <button className={styles.themeToggle} onClick={toggleDarkMode} title="Toggle dark mode" type="button">
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
          <p className={styles.subtitle}>Reset your password</p>

          {error && <div className={styles.error}>{error}</div>}

          {step === 'username' && (
            <form onSubmit={handleUsernameContinue}>
              <div className={styles.formGroup}>
                <label className={styles.label} htmlFor="rp-username">Enter your username to continue.</label>
                <input
                  id="rp-username"
                  type="text"
                  className={styles.input}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                  autoFocus
                />
              </div>
              <button type="submit" className={styles.submitBtn}>Continue</button>
            </form>
          )}

          {step === 'choose' && (
            <>
              <p className={styles.instructions}>How would you like to reset your password?</p>
              <button type="button" className={styles.submitBtn} onClick={() => setStep('email')}>
                Email me a reset link
              </button>
              <div style={{ height: 16 }} />
              <button type="button" className={styles.submitBtn} onClick={() => setStep('totp')}>
                Use your authenticator app
              </button>
              <p className={styles.instructions} style={{ marginTop: 16, fontSize: 12 }}>
                If neither works, contact your administrator.
              </p>
            </>
          )}

          {step === 'email' && (
            <form onSubmit={handleEmailSubmit}>
              <div className={styles.formGroup}>
                <label className={styles.label} htmlFor="rp-email">Enter the email address associated with your account.</label>
                <input
                  id="rp-email"
                  type="email"
                  className={styles.input}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                  autoFocus
                  disabled={emailMutation.isPending}
                />
              </div>
              <button type="submit" className={styles.submitBtn} disabled={emailMutation.isPending}>
                {emailMutation.isPending ? <span className={styles.spinner} /> : 'Send Reset Link'}
              </button>
            </form>
          )}

          {step === 'totp' && (
            <form onSubmit={handleTotpSubmit}>
              <p className={styles.instructions}>
                Enter the 6-digit code from your authenticator app and choose a new password.
              </p>
              <div className={styles.formGroup}>
                <label className={styles.label} htmlFor="rp-code">6-digit code</label>
                <input
                  id="rp-code"
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  className={styles.input}
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  autoFocus
                  required
                />
              </div>
              <div className={styles.formGroup}>
                <label className={styles.label} htmlFor="rp-new">New password</label>
                <input
                  id="rp-new"
                  type="password"
                  className={styles.input}
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  autoComplete="new-password"
                  required
                  minLength={8}
                  maxLength={128}
                />
                {newPassword && <PasswordStrengthMeter password={newPassword} />}
              </div>
              <div className={styles.formGroup}>
                <label className={styles.label} htmlFor="rp-confirm">Confirm new password</label>
                <input
                  id="rp-confirm"
                  type="password"
                  className={styles.input}
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  autoComplete="new-password"
                  required
                  minLength={8}
                  maxLength={128}
                />
              </div>
              <button type="submit" className={styles.submitBtn} disabled={totpMutation.isPending}>
                {totpMutation.isPending ? <span className={styles.spinner} /> : 'Reset password'}
              </button>
            </form>
          )}

          {step === 'submitted' && (
            <div className={styles.successState}>
              {totpMutation.isSuccess ? (
                <>
                  <h3 className={styles.successTitle}>Password reset</h3>
                  <p className={styles.successText}>
                    Your password has been reset. You can now sign in with your new password.
                  </p>
                </>
              ) : (
                <>
                  <h3 className={styles.successTitle}>Check your email</h3>
                  <p className={styles.successText}>
                    If an account exists with that email address, we've sent password reset instructions.
                  </p>
                </>
              )}
            </div>
          )}

          <div className={styles.backLink}>
            <Link to="/login">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="19" y1="12" x2="5" y2="12" /><polyline points="12 19 5 12 12 5" />
              </svg>
              Back to Login
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
