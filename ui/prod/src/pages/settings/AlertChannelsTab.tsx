// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/AlertChannelsTab.tsx
//
// Admin screen for managing alert delivery channels (webhooks today; #919-921
// extend the type→form registry below with more channel types). List with
// Test/Edit/Delete, an Add/Edit modal that renders a config form keyed by
// channel type, and an in-page delete confirm (never the native confirm()).

import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import type { ComponentType } from 'react';
import { useOutletContext } from 'react-router-dom';

import {
  channelsApi,
  useChannels,
  useChannelTypes,
  useCreateChannel,
  useUpdateChannel,
  useDeleteChannel,
} from '@/api/channels';
import type { AlertChannel, ChannelCreate, ChannelUpdate, TestResult } from '@/api/channels';
import { getApiErrorMessage } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { DeleteIconButton, IconButton } from '@/components/ui/IconButton';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { Modal } from '@/components/ui/Modal';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { InlineStatus } from '@/components/ui/InlineStatus';
import type { AlertDeliveryOutletContext } from './AlertDeliveryGroupPage';
import { WebhookChannelForm } from './channels/WebhookChannelForm';
import { EmailChannelForm } from './channels/EmailChannelForm';
import { SyslogChannelForm } from './channels/SyslogChannelForm';
import styles from './AlertChannelsTab.module.css';

// Type → config-form registry: webhook (#918), email (#919), syslog (#920).
// Extend here when adding a new channel type.
const CONFIG_FORMS: Record<
  string,
  ComponentType<{ channel?: AlertChannel; onConfigChange: (config: Record<string, unknown>) => void }>
> = {
  webhook: WebhookChannelForm,
  email: EmailChannelForm,
  syslog: SyslogChannelForm,
};

function defaultConfigForType(type: string): Record<string, unknown> {
  if (type === 'webhook') return { url: '', timeout: 30 };
  if (type === 'email') return { recipients: [] };
  if (type === 'syslog') return { host: '', port: 514, protocol: 'udp', facility: 'local0' };
  return {};
}

type ChannelTestOutcome = TestResult | { ok: false; response_code: null; error: string };

export function AlertChannelsTab() {
  const { data: channels, isLoading } = useChannels();
  const { data: types } = useChannelTypes();
  const { setTabActions } = useOutletContext<AlertDeliveryOutletContext>();

  // Add/Edit modal state. `modalChannel` undefined => create; set => edit.
  const [modalOpen, setModalOpen] = useState(false);
  const [modalChannel, setModalChannel] = useState<AlertChannel | undefined>(undefined);
  const [modalType, setModalType] = useState('webhook');
  const [modalName, setModalName] = useState('');
  const [modalEnabled, setModalEnabled] = useState(true);
  const [modalConfig, setModalConfig] = useState<Record<string, unknown>>({});

  const [confirmDelete, setConfirmDelete] = useState<AlertChannel | null>(null);
  const [deleteError, setDeleteError] = useState('');

  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, ChannelTestOutcome>>({});

  function closeModal() {
    setModalOpen(false);
  }

  const createChannel = useCreateChannel({ onSuccess: closeModal });
  const updateChannel = useUpdateChannel({ onSuccess: closeModal });
  const deleteChannel = useDeleteChannel();

  const openAdd = useCallback(() => {
    // Clear any error left over from a prior failed save so a fresh modal opens clean
    // (submitError reads createChannel.error ?? updateChannel.error, which persist until
    // reset or the next mutate).
    createChannel.reset();
    updateChannel.reset();
    const firstType = types?.[0]?.type ?? 'webhook';
    setModalChannel(undefined);
    setModalType(firstType);
    setModalName('');
    setModalEnabled(true);
    setModalConfig(defaultConfigForType(firstType));
    setModalOpen(true);
  }, [createChannel, updateChannel, types]);

  // The "Add channel" trigger lives on the sub-tab row (rendered by the group
  // page), while its modal state stays here. Keep the handler in a ref so the
  // action is set once — depending on openAdd directly would re-fire the effect
  // every render (mutation objects change identity), setState in the parent, and
  // loop. The ref stays fresh so the button always calls the latest handler.
  const openAddRef = useRef(openAdd);
  openAddRef.current = openAdd;
  useEffect(() => {
    setTabActions(
      <Button variant="primary" size="sm" onClick={() => openAddRef.current()}>
        Add channel
      </Button>,
    );
    return () => setTabActions(null);
  }, [setTabActions]);

  if (isLoading) return <LoadingSpinner />;

  const list = channels ?? [];
  const typeOptions = types ?? [];

  function openEdit(channel: AlertChannel) {
    createChannel.reset();
    updateChannel.reset();
    setModalChannel(channel);
    setModalType(channel.type);
    setModalName(channel.name);
    setModalEnabled(channel.enabled);
    setModalConfig(channel.config);
    setModalOpen(true);
  }

  function handleTypeChange(type: string) {
    setModalType(type);
    setModalConfig(defaultConfigForType(type));
  }

  function handleSave() {
    if (modalChannel) {
      const body: ChannelUpdate = { name: modalName, enabled: modalEnabled, config: modalConfig };
      updateChannel.mutate({ id: modalChannel.id, body });
    } else {
      const body: ChannelCreate = { type: modalType, name: modalName, config: modalConfig };
      createChannel.mutate(body);
    }
  }

  async function handleTest(channel: AlertChannel) {
    setTestingId(channel.id);
    try {
      const result = await channelsApi.test(channel.id);
      setTestResults((prev) => ({ ...prev, [channel.id]: result }));
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [channel.id]: { ok: false, response_code: null, error: getApiErrorMessage(err, 'Test failed') },
      }));
    } finally {
      setTestingId(null);
    }
  }

  function handleDeleteConfirm() {
    if (!confirmDelete) return;
    const name = confirmDelete.name;
    deleteChannel.mutate(confirmDelete.id, {
      onSuccess: () => {
        setDeleteError('');
        setConfirmDelete(null);
      },
      onError: () => {
        setDeleteError(`Failed to delete "${name}"`);
        setConfirmDelete(null);
      },
    });
  }

  const ConfigForm = CONFIG_FORMS[modalType];
  const isSaving = createChannel.isPending || updateChannel.isPending;
  const submitError = createChannel.error ?? updateChannel.error;

  return (
    <>
      {list.length === 0 ? (
        <div className={styles.emptyState}>
          No alert delivery channels configured yet.
        </div>
      ) : (
        <div className={styles.tableWrapper}>
          <table className={styles.table}>
            <thead>
              <tr className={styles.headerRow}>
                <th>Name</th>
                <th>Type</th>
                <th>Enabled</th>
                <th className={styles.actionsHead}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {list.map((channel) => {
                const result = testResults[channel.id];
                return (
                  <Fragment key={channel.id}>
                    <tr className={styles.dataRow}>
                      <td className={styles.nameCell}>{channel.name}</td>
                      <td>{channel.type}</td>
                      <td>
                        <span className={channel.enabled ? styles.enabledYes : styles.enabledNo}>
                          {channel.enabled ? 'Yes' : 'No'}
                        </span>
                      </td>
                      <td className={styles.actionsCell}>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => void handleTest(channel)}
                          disabled={testingId === channel.id}
                        >
                          {testingId === channel.id ? 'Testing…' : 'Test'}
                        </Button>
                        <IconButton
                          onClick={() => openEdit(channel)}
                          title="Edit"
                          aria-label={`Edit ${channel.name}`}
                        >
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                        </IconButton>
                        <DeleteIconButton
                          onClick={() => setConfirmDelete(channel)}
                          title="Delete"
                          aria-label={`Delete ${channel.name}`}
                        />
                      </td>
                    </tr>
                    {result && (
                      <tr>
                        <td colSpan={4} className={styles.testResultRow}>
                          <span className={result.ok ? styles.success : styles.failure} role="status">
                            {result.ok
                              ? `Test succeeded${result.response_code != null ? ` — HTTP ${result.response_code}` : ''}`
                              : `Test failed: ${result.error ?? 'unknown error'}`}
                          </span>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {deleteError && <p className={styles.deleteError}>{deleteError}</p>}

      {modalOpen && (
        <Modal
          isOpen
          title={modalChannel ? 'Edit channel' : 'Add channel'}
          onClose={closeModal}
          size="md"
          showCloseButton
        >
          <div className={styles.field}>
            <label className={styles.label} htmlFor="channel-name">Name</label>
            <input
              id="channel-name"
              className={styles.input}
              value={modalName}
              onChange={(e) => setModalName(e.target.value)}
            />
          </div>

          {!modalChannel && (
            <div className={styles.field}>
              <label className={styles.label} htmlFor="channel-type">Type</label>
              <select
                id="channel-type"
                className={styles.select}
                value={modalType}
                onChange={(e) => handleTypeChange(e.target.value)}
              >
                {typeOptions.map((t) => (
                  <option key={t.type} value={t.type}>{t.label}</option>
                ))}
              </select>
            </div>
          )}

          {modalChannel && (
            <label className={styles.enableRow}>
              <input
                type="checkbox"
                checked={modalEnabled}
                onChange={(e) => setModalEnabled(e.target.checked)}
              />
              Enabled
            </label>
          )}

          {ConfigForm ? (
            <ConfigForm key={modalChannel?.id ?? modalType} channel={modalChannel} onConfigChange={setModalConfig} />
          ) : (
            <p className={styles.hint}>No configuration form available for type &quot;{modalType}&quot;.</p>
          )}

          <InlineStatus
            status={
              submitError
                ? { kind: 'error', message: getApiErrorMessage(submitError, 'Failed to save channel') }
                : null
            }
          />

          <div className={styles.footer}>
            <Button variant="ghost" onClick={closeModal}>Cancel</Button>
            <Button onClick={handleSave} disabled={!modalName.trim() || isSaving} loading={isSaving}>
              Save
            </Button>
          </div>
        </Modal>
      )}

      <ConfirmDialog
        isOpen={confirmDelete !== null}
        title="Delete channel"
        message={`Delete channel '${confirmDelete?.name}'? This cannot be undone.`}
        confirmLabel="Delete"
        variant="danger"
        onConfirm={handleDeleteConfirm}
        onCancel={() => setConfirmDelete(null)}
      />
    </>
  );
}
