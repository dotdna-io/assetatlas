// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';

import { useTestLdap } from '@/api/auth';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { CategorySettings } from './CategorySettings';
import styles from './LdapSettingsTab.module.css';

export function LdapSettingsTab() {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const testLdap = useTestLdap();

  function openDialog() {
    setUsername('');
    setPassword('');
    testLdap.reset();
    setDialogOpen(true);
  }

  function closeDialog() {
    setDialogOpen(false);
    testLdap.reset();
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!username || !password) return;
    testLdap.mutate({ username, password });
  }

  const result = testLdap.data;
  const testError = testLdap.error;

  return (
    <div className={styles.container}>
      <CategorySettings
        category="ldap"
        title="Configure LDAP / Active Directory authentication. When enabled, login attempts are verified against the directory first; local password verification is used as a fallback."
        descriptive
        layout="stack"
        masterToggle={{
          settingKey: 'enabled',
          label: 'Enable LDAP authentication',
          // Mirrors LdapService.is_enabled() (src/auth/services/ldap_service.py):
          // the flag AND a server URL. With the flag on and no URL, sign-ins
          // silently never reach the directory — the state this note exists to
          // make visible.
          requiredKeys: [{ key: 'server_url', label: 'Server URL' }],
          offNote: 'Disabled — sign-ins are checked against local passwords only.',
          onNote: 'Enabled — sign-ins are checked against the directory first.',
        }}
        leftActions={
          <Button variant="primary" onClick={openDialog}>
            Test connection
          </Button>
        }
      />

      <Modal
        isOpen={dialogOpen}
        onClose={closeDialog}
        title="Test LDAP connection"
        showCloseButton={false}
      >
        <p className={styles.dialogHint}>
          Enter credentials to perform a live bind+search against the
          directory. Uses the currently-saved settings.
        </p>
        <form onSubmit={handleSubmit}>
          <div className={styles.row}>
            <label htmlFor="ldap_test_username">Username</label>
            <input
              id="ldap_test_username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="jsmith or jsmith@corp.com"
              autoComplete="off"
            />
          </div>
          <div className={styles.row}>
            <label htmlFor="ldap_test_password">Password</label>
            <input
              id="ldap_test_password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </div>

          {result && (
            <div
              role="status"
              className={result.success ? styles.success : styles.failure}
            >
              {result.success ? (
                <ul className={styles.resultList}>
                  <li>
                    <strong>User DN:</strong>{' '}
                    <code>{result.user_dn ?? '—'}</code>
                  </li>
                  <li>
                    <strong>Mapped role:</strong>{' '}
                    <Badge variant="info">{result.mapped_role ?? 'none'}</Badge>
                  </li>
                  {Object.keys(result.attributes).length > 0 && (
                    <li>
                      <strong>Attributes:</strong>
                      <ul className={styles.attrList}>
                        {Object.entries(result.attributes).map(([k, v]) => (
                          <li key={k}>
                            <code>{k}</code>: {String(v)}
                          </li>
                        ))}
                      </ul>
                    </li>
                  )}
                </ul>
              ) : (
                <>
                  <strong>Connection failed:</strong>
                  <ul className={styles.errorList}>
                    {(result.errors.length > 0 ? result.errors : ['Unknown error']).map(
                      (err, i) => (
                        <li key={i}>{err}</li>
                      ),
                    )}
                  </ul>
                </>
              )}
            </div>
          )}

          {testError && (
            <div role="status" className={styles.failure}>
              {testError.message || 'Request failed'}
            </div>
          )}

          <div className={styles.dialogActions}>
            <Button
              type="submit"
              disabled={testLdap.isPending || !username || !password}
            >
              {testLdap.isPending ? 'Testing…' : 'Test'}
            </Button>
            <Button type="button" variant="secondary" onClick={closeDialog}>
              Close
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
