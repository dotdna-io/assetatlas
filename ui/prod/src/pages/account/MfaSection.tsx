// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import type { FormEvent } from 'react';
import { QRCode } from 'react-qrcode-logo';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { useAuth } from '@/auth';
import {
  useAccountMfa,
  useSetupTotpSelf,
  useVerifyTotpSelf,
  useRemoveMfaMethod,
  useRenameMfaMethod,
  type AccountMfaMethod,
  type MfaSetupResponse,
} from '@/api/auth';
import { getApiErrorMessage } from '@/api/client';
import enrollStyles from '../profile/MfaEnrollment.module.css';
import styles from './AccountPage.module.css';

export function MfaSection() {
  const { user } = useAuth();
  const list = useAccountMfa();
  const setup = useSetupTotpSelf();
  const verify = useVerifyTotpSelf();
  const remove = useRemoveMfaMethod();
  const rename = useRenameMfaMethod();
  const [enrollData, setEnrollData] = useState<MfaSetupResponse | null>(null);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [pendingRemoval, setPendingRemoval] = useState<AccountMfaMethod | null>(null);

  if (user?.is_ldap_user || user?.is_oidc_user) {
    return (
      <section className={styles.section}>
        <h2 className={styles.sectionHeading}>Two-factor authentication</h2>
        <p className={styles.sectionDescription}>
          MFA enrollment is not yet available for accounts authenticated via SSO/LDAP.
          Contact your identity provider administrator about enabling MFA at the directory level.
        </p>
      </section>
    );
  }

  const allMethods: AccountMfaMethod[] = list.data?.methods ?? [];
  // Only show verified methods in the list. Pending rows are an internal
  // mid-enrollment artifact — they live in `enrollData` while the user
  // completes the wizard, then become enabled rows on verify.
  const enrolled = allMethods.filter((m) => m.enabled);
  const enabledCount = enrolled.length;

  async function handleStartEnroll() {
    setError('');
    try {
      const data = await setup.mutateAsync();
      setEnrollData(data);
      setCode('');
    } catch (e) {
      setError(getApiErrorMessage(e, 'Setup failed'));
    }
  }

  async function handleVerify(e: FormEvent) {
    e.preventDefault();
    setError('');
    try {
      await verify.mutateAsync({ code: code.trim() });
      setEnrollData(null);
      setCode('');
    } catch (err) {
      setError(getApiErrorMessage(err, 'Verification failed'));
    }
  }

  async function confirmRemove() {
    const method = pendingRemoval;
    if (!method) return;
    setPendingRemoval(null);
    setError('');
    try {
      await remove.mutateAsync(method.id);
    } catch (e) {
      setError(getApiErrorMessage(e, 'Remove failed'));
    }
  }

  function startRename(method: AccountMfaMethod) {
    setRenamingId(method.id);
    setRenameDraft(method.label);
    setError('');
  }

  function cancelRename() {
    setRenamingId(null);
    setRenameDraft('');
  }

  async function commitRename(e: FormEvent) {
    e.preventDefault();
    if (!renamingId) return;
    const trimmed = renameDraft.trim();
    if (!trimmed) {
      setError('Label cannot be empty.');
      return;
    }
    setError('');
    try {
      await rename.mutateAsync({ methodId: renamingId, label: trimmed });
      cancelRename();
    } catch (err) {
      setError(getApiErrorMessage(err, 'Rename failed'));
    }
  }

  // ---- Enrollment wizard view -------------------------------------------
  if (enrollData) {
    return (
      <section className={styles.section}>
        <h2 className={styles.sectionHeading}>Set up your authenticator</h2>
        <ol className={enrollStyles.steps}>
          <li>Open your authenticator app and add a new account.</li>
          <li>Scan this QR code, or enter the secret manually.</li>
          <li>Enter the 6-digit code shown in the app to confirm.</li>
        </ol>
        <div className={`${enrollStyles.qrBlock} ${styles.qrBlockCentered}`}>
          <QRCode
            value={enrollData.provisioning_uri}
            size={208}
            ecLevel="M"
            qrStyle="dots"
            eyeRadius={8}
            fgColor="#7CB342"
            eyeColor="#374151"
            bgColor="transparent"
            quietZone={12}
          />
          <div className={`${enrollStyles.secretRow} ${styles.secretRowCentered}`}>
            <span className={enrollStyles.secretLabel}>Manual entry secret:</span>
            <code className={enrollStyles.secret}>{enrollData.secret}</code>
          </div>
        </div>
        <form onSubmit={handleVerify}>
          <div className={styles.formGroup}>
            <label htmlFor="acct-mfa-code" className={styles.label}>6-digit code</label>
            <input
              id="acct-mfa-code"
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
            <Button type="submit" disabled={verify.isPending}>
              {verify.isPending ? 'Verifying…' : 'Verify and enable'}
            </Button>
            <Button type="button" variant="secondary" onClick={() => { setEnrollData(null); setCode(''); setError(''); }}>
              Cancel
            </Button>
          </div>
        </form>
        {error && <div className={styles.error}>{error}</div>}
      </section>
    );
  }

  // ---- Default view: list + add ------------------------------------------
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionHeading}>Two-factor authentication</h2>
      <p className={styles.sectionDescription}>
        Enroll one or more authenticator apps (Google Authenticator, Authy, 1Password,
        etc.). At sign-in you can use the code from any enrolled method — handy if
        your phone is unavailable.
      </p>

      {enrolled.length === 0 ? (
        <div className={styles.actions}>
          <Button onClick={handleStartEnroll} disabled={setup.isPending || list.isLoading}>
            {setup.isPending ? 'Starting…' : 'Add an authenticator app'}
          </Button>
        </div>
      ) : (
        <>
          <ul className={styles.methodList}>
            {enrolled.map((m) => {
              const isRenaming = renamingId === m.id;
              const removeWouldStrand = enabledCount <= 1;
              return (
                <li key={m.id} className={styles.methodRow}>
                  <div className={styles.methodInfo}>
                    {isRenaming ? (
                      <form onSubmit={commitRename}>
                        <input
                          className={styles.renameInput}
                          value={renameDraft}
                          onChange={(e) => setRenameDraft(e.target.value)}
                          maxLength={64}
                          autoFocus
                          aria-label="Method label"
                        />
                      </form>
                    ) : (
                      <span className={styles.methodLabel}>{m.label}</span>
                    )}
                    {m.created_at && (
                      <span className={styles.methodMeta}>
                        Added {new Date(m.created_at).toLocaleDateString()}
                      </span>
                    )}
                  </div>
                  <div className={styles.methodActions}>
                    {isRenaming ? (
                      <>
                        <Button
                          type="button"
                          onClick={commitRename}
                          disabled={rename.isPending}
                        >
                          {rename.isPending ? 'Saving…' : 'Save'}
                        </Button>
                        <Button
                          type="button"
                          variant="secondary"
                          onClick={cancelRename}
                          disabled={rename.isPending}
                        >
                          Cancel
                        </Button>
                      </>
                    ) : (
                      <>
                        <Button type="button" variant="secondary" onClick={() => startRename(m)}>
                          Rename
                        </Button>
                        <span title={removeWouldStrand ? 'You must keep at least one MFA method' : ''}>
                          <Button
                            variant="danger"
                            disabled={removeWouldStrand || remove.isPending}
                            onClick={() => setPendingRemoval(m)}
                          >
                            Remove
                          </Button>
                        </span>
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          <div className={styles.addAnother}>
            <Button
              variant="secondary"
              onClick={handleStartEnroll}
              disabled={setup.isPending}
            >
              {setup.isPending ? 'Starting…' : 'Add another method'}
            </Button>
          </div>
        </>
      )}
      {error && <div className={styles.error}>{error}</div>}

      <ConfirmDialog
        isOpen={pendingRemoval !== null}
        title="Remove this method?"
        message={
          pendingRemoval
            ? `Remove "${pendingRemoval.label}"? You will need a different MFA method to sign in.`
            : ''
        }
        confirmLabel="Remove"
        variant="danger"
        onConfirm={confirmRemove}
        onCancel={() => setPendingRemoval(null)}
      />
    </section>
  );
}
