// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';

import { useTestOidcDiscovery } from '@/api/auth';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { CategorySettings } from './CategorySettings';
import styles from './OidcSettingsTab.module.css';

export function OidcSettingsTab() {
  const [dialogOpen, setDialogOpen] = useState(false);
  const testDiscovery = useTestOidcDiscovery();

  function openDialog() {
    testDiscovery.reset();
    testDiscovery.mutate();
    setDialogOpen(true);
  }

  function closeDialog() {
    setDialogOpen(false);
    testDiscovery.reset();
  }

  const result = testDiscovery.data;
  const testError = testDiscovery.error;

  return (
    <div className={styles.container}>
      <CategorySettings
        category="oidc"
        title="Configure OpenID Connect (OIDC) Single Sign-On. When enabled, users can sign in via your identity provider (e.g. Entra ID, Okta, Keycloak)."
        descriptive
        layout="stack"
        masterToggle={{
          settingKey: 'enabled',
          label: 'Enable OIDC single sign-on',
          // Mirrors OidcService.is_enabled() (src/auth/services/oidc_service.py):
          // the flag AND all four IdP parameters. Miss any one and
          // /auth/sso-providers reports SSO off, so the login page drops the
          // button with nothing anywhere to explain it.
          requiredKeys: [
            { key: 'discovery_url', label: 'Discovery URL' },
            { key: 'client_id', label: 'Client ID' },
            { key: 'client_secret', label: 'Client secret' },
            { key: 'redirect_uri', label: 'Redirect URI' },
          ],
          offNote: 'Disabled — the sign-in page shows no SSO button.',
          onNote: 'Enabled — the sign-in page offers SSO through your identity provider.',
        }}
        leftActions={
          <Button
            variant="primary"
            onClick={openDialog}
            disabled={testDiscovery.isPending}
          >
            {testDiscovery.isPending ? 'Testing…' : 'Test discovery'}
          </Button>
        }
      />

      <Modal
        isOpen={dialogOpen}
        onClose={closeDialog}
        title="OIDC discovery test"
        showCloseButton={false}
      >
        <p className={styles.dialogHint}>
          Fetches the OIDC discovery document from the configured issuer URL.
          Uses the currently-saved settings.
        </p>

        {testDiscovery.isPending && (
          <p>Connecting to discovery URL…</p>
        )}

        {result && (
          <div
            role="status"
            className={result.success ? styles.success : styles.failure}
          >
            {result.success ? (
              <ul className={styles.resultList}>
                <li>
                  <strong>Issuer:</strong>{' '}
                  <code>{result.issuer ?? '—'}</code>
                </li>
                <li>
                  <strong>Authorization endpoint:</strong>{' '}
                  <code>{result.authorization_endpoint ?? '—'}</code>
                </li>
                <li>
                  <strong>Token endpoint:</strong>{' '}
                  <code>{result.token_endpoint ?? '—'}</code>
                </li>
                <li>
                  <strong>JWKS URI:</strong>{' '}
                  <code>{result.jwks_uri ?? '—'}</code>
                </li>
              </ul>
            ) : (
              <>
                <strong>Discovery failed:</strong>
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
          <Button type="button" onClick={closeDialog}>
            Close
          </Button>
        </div>
      </Modal>
    </div>
  );
}
