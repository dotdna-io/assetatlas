// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useCallback } from 'react';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { ApiKeyExpiryPicker } from './ApiKeyExpiryPicker';
import {
  useApiKeys,
  useCreateApiKey,
  useUpdateApiKey,
  useDeleteApiKey,
} from '@/api/api-keys';
import type { ApiKey, CreateApiKeyResponse } from '@/api/api-keys';
import styles from './apiKeys.module.css';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string | null): string {
  if (!iso) return '--';
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '--';
  const d = new Date(iso);
  return (
    d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) +
    ' ' +
    d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  );
}

function isExpired(expiresAt: string): boolean {
  return new Date(expiresAt) < new Date();
}

function getStatus(key: ApiKey): 'active' | 'expired' | 'revoked' {
  if (!key.is_active) return 'revoked';
  if (isExpired(key.expires_at)) return 'expired';
  return 'active';
}

const STATUS_LABEL: Record<string, string> = {
  active: 'Active',
  expired: 'Expired',
  revoked: 'Revoked',
};

const STATUS_CLASS: Record<string, string> = {
  active: styles.badgeActive,
  expired: styles.badgeExpired,
  revoked: styles.badgeRevoked,
};

function addDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().split('T')[0];
}

const EXPIRY_PRESETS = [
  { label: '30 days', days: 30 },
  { label: '90 days', days: 90 },
  { label: '180 days', days: 180 },
  { label: '1 year', days: 365 },
] as const;

// ---------------------------------------------------------------------------
// Modal state types
// ---------------------------------------------------------------------------

type ModalState =
  | { type: null; key: null }
  | { type: 'create'; key: null }
  | { type: 'reveal'; key: null }
  | { type: 'revoke'; key: ApiKey }
  | { type: 'delete'; key: ApiKey };

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ApiKeysTab() {
  const [status, setStatus] = useInlineStatus();
  const { data, isLoading } = useApiKeys();

  const [modal, setModal] = useState<ModalState>({ type: null, key: null });

  // Create form state
  const [name, setName] = useState('');
  const [expiresAt, setExpiresAt] = useState(() => addDays(90));
  const [activePreset, setActivePreset] = useState<number | null>(90);

  // Key reveal state
  const [revealedKey, setRevealedKey] = useState<CreateApiKeyResponse | null>(null);
  const [acked, setAcked] = useState(false);
  const [copied, setCopied] = useState(false);

  function closeModal() {
    setModal({ type: null, key: null });
  }

  function openCreate() {
    setName('');
    setExpiresAt(addDays(90));
    setActivePreset(90);
    setModal({ type: 'create', key: null });
  }

  const handlePreset = useCallback((days: number) => {
    setActivePreset(days);
    setExpiresAt(addDays(days));
  }, []);

  const handleCustomDate = useCallback((value: string) => {
    setActivePreset(null);
    setExpiresAt(value);
  }, []);

  // ------- Mutations -------

  const createMutation = useCreateApiKey({
    onSuccess: (created) => {
      // No success line: the reveal modal below opens immediately and is titled
      // "API Key Created". Cleared rather than left alone so a failed earlier
      // attempt does not leave its error sitting above a successful one.
      setStatus(null);
      setRevealedKey(created);
      setAcked(false);
      setCopied(false);
      setModal({ type: 'reveal', key: null });
    },
    onError: (err) => setStatus({ kind: 'error', message: err.message }),
  });

  // For revoke we need the key id from the modal — use a stable id ref
  const revokeId = modal.type === 'revoke' ? modal.key.id : '';
  const revokeMutation = useUpdateApiKey(revokeId, {
    onSuccess: () => {
      setStatus(null); // the row's Status badge flips to Revoked — that is the feedback
      closeModal();
    },
    onError: (err) => setStatus({ kind: 'error', message: err.message }),
  });

  const deleteMutation = useDeleteApiKey({
    onSuccess: () => {
      setStatus(null); // the row disappears from the table — that is the feedback
      closeModal();
    },
    onError: (err) => setStatus({ kind: 'error', message: err.message }),
  });

  // ------- Handlers -------

  function handleCreateSubmit() {
    if (!name.trim()) return;
    createMutation.mutate({
      name: name.trim(),
      expires_at: new Date(expiresAt + 'T23:59:59Z').toISOString(),
    });
  }

  async function handleCopy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      // The copy button itself flips to "Copied" — the action tag keeps the
      // overlay from doubling that feedback.
      setStatus({ kind: 'success', message: 'Copied to clipboard', action: 'copy' });
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setStatus({ kind: 'error', message: 'Failed to copy' });
    }
  }

  // ------- Render -------

  const keys = data?.api_keys ?? [];

  return (
    <div className={styles.page}>
      <InlineStatus status={status} />
      {/* Header */}
      <div className={styles.header}>
        <Button variant="primary" onClick={openCreate}>
          Create API Key
        </Button>
      </div>

      {/* Table */}
      <div className={styles.tableCard}>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}>Name</th>
                <th className={styles.th}>Key Prefix</th>
                <th className={styles.th}>Created By</th>
                <th className={styles.th}>Expires At</th>
                <th className={styles.th}>Last Used</th>
                <th className={styles.th}>Status</th>
                <th className={`${styles.th} ${styles.thRight}`}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr>
                  <td className={styles.emptyCell} colSpan={7}>
                    Loading...
                  </td>
                </tr>
              ) : keys.length === 0 ? (
                <tr>
                  <td className={styles.emptyCell} colSpan={7}>
                    No API keys created yet.
                  </td>
                </tr>
              ) : (
                keys.map((k) => {
                  const status = getStatus(k);
                  return (
                    <tr key={k.id}>
                      <td className={styles.td}>{k.name}</td>
                      <td className={styles.td}>
                        <span className={styles.keyPrefix}>{k.key_prefix}...</span>
                      </td>
                      <td className={styles.td}>
                        <span className={styles.secondary}>{k.created_by ?? '--'}</span>
                      </td>
                      <td className={styles.td}>
                        <span className={styles.secondary}>{formatDate(k.expires_at)}</span>
                      </td>
                      <td className={styles.td}>
                        <span className={styles.muted}>
                          {k.last_used_at ? formatDateTime(k.last_used_at) : 'Never'}
                        </span>
                      </td>
                      <td className={styles.td}>
                        <span className={`${styles.badge} ${STATUS_CLASS[status]}`}>
                          {STATUS_LABEL[status]}
                        </span>
                      </td>
                      <td className={styles.td}>
                        <div className={styles.actionsCell}>
                          {k.is_active && !isExpired(k.expires_at) && (
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => setModal({ type: 'revoke', key: k })}
                            >
                              Revoke
                            </Button>
                          )}
                          <Button
                            variant="danger"
                            size="sm"
                            onClick={() => setModal({ type: 'delete', key: k })}
                          >
                            Delete
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Create Modal */}
      <Modal
        isOpen={modal.type === 'create'}
        onClose={closeModal}
        title="Create API Key"
        size="sm"
      >
        <div className={styles.formGroup}>
          <label className={styles.formLabel}>
            Name <span className={styles.required}>*</span>
          </label>
          <input
            className={styles.formInput}
            type="text"
            placeholder="e.g. Production SIEM"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Expiration</label>
          <ApiKeyExpiryPicker
            presets={EXPIRY_PRESETS}
            activePreset={activePreset}
            expiresAt={expiresAt}
            onPresetChange={handlePreset}
            onCustomDate={handleCustomDate}
          />
        </div>

        <div className={styles.modalFooter}>
          <Button variant="secondary" onClick={closeModal}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleCreateSubmit}
            disabled={!name.trim()}
            loading={createMutation.isPending}
          >
            Create Key
          </Button>
        </div>
      </Modal>

      {/* Key Reveal Modal */}
      <Modal
        isOpen={modal.type === 'reveal'}
        onClose={() => {
          if (!acked) return; // prevent closing before acknowledgment
        }}
        title="API Key Created"
        size="md"
      >
        {revealedKey && (
          <div className={styles.keyReveal}>
            <div className={styles.warningBox}>
              <svg className={styles.warningIcon} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
                <line x1="12" y1="9" x2="12" y2="13" />
                <line x1="12" y1="17" x2="12.01" y2="17" />
              </svg>
              <span>
                This key will not be shown again. Copy it now and store it securely.
              </span>
            </div>

            <div className={styles.keyMeta}>
              <div>
                <span className={styles.keyMetaLabel}>Name</span>
                <div>{revealedKey.name}</div>
              </div>
              <div>
                <span className={styles.keyMetaLabel}>Expires</span>
                <div>{formatDate(revealedKey.expires_at)}</div>
              </div>
            </div>

            <div>
              <span className={styles.keyMetaLabel}>API Key</span>
              <div className={styles.keyContainer}>
                <code className={styles.keyValue}>{revealedKey.key}</code>
                <button
                  type="button"
                  className={styles.copyBtn}
                  onClick={() => handleCopy(revealedKey.key)}
                >
                  {copied ? (
                    <>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                      Copied
                    </>
                  ) : (
                    <>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                        <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
                      </svg>
                      Copy
                    </>
                  )}
                </button>
              </div>
            </div>

            <label className={styles.ackGroup}>
              <input
                type="checkbox"
                checked={acked}
                onChange={(e) => setAcked(e.target.checked)}
              />
              I have copied and securely stored this key
            </label>

            <div className={styles.modalFooter}>
              <Button
                variant="primary"
                disabled={!acked}
                onClick={closeModal}
              >
                Done
              </Button>
            </div>
          </div>
        )}
      </Modal>

      {/* Revoke Confirmation */}
      <ConfirmDialog
        isOpen={modal.type === 'revoke'}
        onCancel={closeModal}
        onConfirm={() => {
          if (modal.type === 'revoke') {
            revokeMutation.mutate({ is_active: false });
          }
        }}
        title="Revoke API Key"
        message={`Revoke "${modal.type === 'revoke' ? modal.key.name : ''}"? Any system using this key will immediately lose access. This cannot be undone.`}
        confirmLabel="Revoke Key"
        variant="warning"
      />

      {/* Delete Confirmation */}
      <ConfirmDialog
        isOpen={modal.type === 'delete'}
        onCancel={closeModal}
        onConfirm={() => {
          if (modal.type === 'delete') {
            deleteMutation.mutate(modal.key.id);
          }
        }}
        title="Delete API Key"
        message={`Permanently delete "${modal.type === 'delete' ? modal.key.name : ''}"? This action cannot be undone.`}
        confirmLabel="Delete Key"
        variant="danger"
      />
    </div>
  );
}
