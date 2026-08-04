// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import {
  useMfaStatus,
  useSetupMfa,
  useVerifyMfa,
  useDisableMfa,
} from '@/api/auth';
import type { MfaSetupResponse } from '@/api/auth';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { useAuth } from '@/auth';
import styles from './MfaEnrollment.module.css';

// ---- Disable MFA dialog ----------------------------------------------------

interface DisableDialogProps {
  isPending: boolean;
  onConfirm: (password: string, code: string) => void;
  onCancel: () => void;
}

function DisableDialog({ isPending, onConfirm, onCancel }: DisableDialogProps) {
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');

  return (
    <Modal isOpen={true} onClose={onCancel} title="Disable two-factor authentication" size="sm">
      <p className={styles.description}>
        Enter your password and a current 6-digit code to disable MFA on this account.
      </p>
      <div className={styles.formGroup}>
        <label className={styles.label} htmlFor="disable-password">Password</label>
        <input
          id="disable-password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Your password"
          autoComplete="current-password"
          autoFocus
          className={styles.input}
        />
      </div>
      <div className={styles.formGroup}>
        <label className={styles.label} htmlFor="disable-code">6-digit code</label>
        <input
          id="disable-code"
          type="text"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="123456"
          autoComplete="one-time-code"
          className={styles.codeInput}
        />
      </div>
      <div className={styles.actions}>
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
        <Button
          variant="danger"
          onClick={() => onConfirm(password, code)}
          disabled={isPending || !password || !code.trim()}
        >
          {isPending ? 'Disabling…' : 'Disable'}
        </Button>
      </div>
    </Modal>
  );
}

// ---- Main MfaEnrollment component ------------------------------------------

export function MfaEnrollment() {
  const { user } = useAuth();
  const status = useMfaStatus();
  const setupMfa = useSetupMfa();
  const verifyMfa = useVerifyMfa();
  const disableMfa = useDisableMfa();

  const [setupData, setSetupData] = useState<MfaSetupResponse | null>(null);
  const [code, setCode] = useState('');
  const [showDisable, setShowDisable] = useState(false);
  const [error, setError] = useState('');

  // ---- SSO users (LDAP or OIDC) cannot enroll in MFA here ---------------
  if (user?.is_ldap_user || user?.is_oidc_user) {
    return (
      <div className={styles.section}>
        <h2 className={styles.heading}>Two-factor authentication</h2>
        <p className={styles.description}>
          MFA enrollment is not yet available for accounts authenticated via SSO/LDAP.
          Contact your identity provider administrator about enabling MFA at the directory level.
        </p>
      </div>
    );
  }

  const enabled = status.data?.enabled === true;

  // ---- Disabled state (no setup in progress) ------------------------------
  if (!enabled && !setupData) {
    return (
      <div className={styles.section}>
        <h2 className={styles.heading}>Two-factor authentication</h2>
        <p className={styles.description}>
          Add an extra layer of security by requiring a code from an authenticator app
          (Google Authenticator, Authy, 1Password, etc.) when you sign in.
        </p>
        <Button
          onClick={async () => {
            setError('');
            try {
              const data = await setupMfa.mutateAsync();
              setSetupData(data);
            } catch (e) {
              setError(e instanceof Error ? e.message : 'Setup failed');
            }
          }}
          disabled={setupMfa.isPending || status.isLoading}
        >
          {setupMfa.isPending ? 'Starting…' : 'Enable two-factor authentication'}
        </Button>
        {error && <div className={styles.error}>{error}</div>}
      </div>
    );
  }

  // ---- Enrollment step (setup started, not yet verified) ------------------
  if (!enabled && setupData) {
    return (
      <div className={styles.section}>
        <h2 className={styles.heading}>Set up your authenticator</h2>
        <ol className={styles.steps}>
          <li>Open your authenticator app and add a new account.</li>
          <li>Scan this QR code, or enter the secret manually.</li>
          <li>Enter the 6-digit code shown in the app to confirm.</li>
        </ol>

        <div className={styles.qrBlock}>
          <QRCodeSVG
            value={setupData.provisioning_uri}
            size={192}
            aria-label="Scan with your authenticator app"
          />
          <div className={styles.secretRow}>
            <span className={styles.secretLabel}>Manual entry secret:</span>
            <code className={styles.secret}>{setupData.secret}</code>
          </div>
        </div>

        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setError('');
            try {
              await verifyMfa.mutateAsync({ code: code.trim() });
              setSetupData(null);
              setCode('');
            } catch (err) {
              setError(err instanceof Error ? err.message : 'Verification failed');
            }
          }}
        >
          <div className={styles.formGroup}>
            <label className={styles.label} htmlFor="verify-code">6-digit code</label>
            <input
              id="verify-code"
              type="text"
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              autoFocus
              required
              className={styles.codeInput}
            />
          </div>
          <div className={styles.actions}>
            <Button type="submit" disabled={verifyMfa.isPending}>
              {verifyMfa.isPending ? 'Verifying…' : 'Verify and enable'}
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => { setSetupData(null); setCode(''); setError(''); }}
            >
              Cancel
            </Button>
          </div>
        </form>
        {error && <div className={styles.error}>{error}</div>}
      </div>
    );
  }

  // ---- Enabled state -------------------------------------------------------
  return (
    <div className={styles.section}>
      <h2 className={styles.heading}>Two-factor authentication</h2>
      <p className={styles.statusEnabled}>
        Two-factor authentication is <strong>enabled</strong>.
      </p>
      <div className={styles.actions}>
        <Button variant="danger" onClick={() => setShowDisable(true)}>
          Disable two-factor authentication
        </Button>
      </div>

      {showDisable && (
        <DisableDialog
          isPending={disableMfa.isPending}
          onConfirm={async (pw, dc) => {
            setError('');
            try {
              await disableMfa.mutateAsync({ password: pw, code: dc });
              setShowDisable(false);
            } catch (e) {
              setError(e instanceof Error ? e.message : 'Disable failed');
            }
          }}
          onCancel={() => setShowDisable(false)}
        />
      )}

      {error && <div className={styles.error}>{error}</div>}
    </div>
  );
}
