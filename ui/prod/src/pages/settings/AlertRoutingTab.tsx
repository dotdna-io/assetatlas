// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/AlertRoutingTab.tsx
//
// Admin screen for the Alert Delivery -> Routing sub-tab (#918 Task 8).
// Assigns delivery channels + a per-channel minimum severity to each rule
// category. AssetAtlas (the in-app alerts view) renders as an always-on
// default per category — it is not a channel, has no min-severity control,
// and is never included in the save payload.

import { useState } from 'react';
import {
  useRouting,
  useChannels,
  useSetCategoryRouting,
  ROUTING_CATEGORIES,
} from '@/api/channels';
import type { RouteInput, Severity } from '@/api/channels';
import { getApiErrorMessage } from '@/api/client';
import { SettingsSection, SettingsRow, SettingsRowGrid } from '@/components/settings';
import { Button } from '@/components/ui/Button';
import { IconButton } from '@/components/ui/IconButton';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { InlineStatus } from '@/components/ui/InlineStatus';
import { SEVERITIES, SEVERITY_LABELS } from '@/constants/severity';
import styles from './AlertRoutingTab.module.css';

function toRouteInputs(rows: RouteInputLike[] | undefined): RouteInput[] {
  return (rows ?? []).map((r) => ({ channel_id: r.channel_id, min_severity: r.min_severity }));
}

// Shape shared by RouteRow (server) and RouteInput (draft) — only the two
// fields this tab reads/writes.
interface RouteInputLike {
  channel_id: string;
  min_severity: Severity;
}

export function AlertRoutingTab() {
  const { data: routingData, isLoading: routingLoading } = useRouting();
  const { data: channels, isLoading: channelsLoading } = useChannels();
  const setRouting = useSetCategoryRouting();

  // Per-category unsaved edits, keyed by category key. An absent entry means
  // the row reflects the server's routing (useRouting()) as-is; a save clears
  // the category's entry so the next render falls back to the (now refetched)
  // server value. Mirrors CategorySettings' "edits" overlay pattern.
  const [drafts, setDrafts] = useState<Record<string, RouteInput[]>>({});
  const [savingCategory, setSavingCategory] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});

  if (routingLoading || channelsLoading) {
    return <LoadingSpinner />;
  }

  const allChannels = channels ?? [];

  function channelName(channelId: string): string {
    return allChannels.find((c) => c.id === channelId)?.name ?? channelId;
  }

  function rowsFor(categoryKey: string): RouteInput[] {
    return drafts[categoryKey] ?? toRouteInputs(routingData?.[categoryKey]);
  }

  function addChannel(categoryKey: string, channelId: string) {
    if (!channelId) return;
    const current = rowsFor(categoryKey);
    if (current.some((r) => r.channel_id === channelId)) return;
    setDrafts((prev) => ({
      ...prev,
      [categoryKey]: [...current, { channel_id: channelId, min_severity: 'medium' }],
    }));
  }

  function removeChannel(categoryKey: string, channelId: string) {
    setDrafts((prev) => ({
      ...prev,
      [categoryKey]: rowsFor(categoryKey).filter((r) => r.channel_id !== channelId),
    }));
  }

  function setSeverity(categoryKey: string, channelId: string, severity: Severity) {
    setDrafts((prev) => ({
      ...prev,
      [categoryKey]: rowsFor(categoryKey).map((r) =>
        r.channel_id === channelId ? { ...r, min_severity: severity } : r,
      ),
    }));
  }

  function handleSave(categoryKey: string) {
    const payload = rowsFor(categoryKey);
    setSavingCategory(categoryKey);
    setRowErrors((prev) => ({ ...prev, [categoryKey]: '' }));
    setRouting.mutate(
      { category: categoryKey, channels: payload },
      {
        onSuccess: () => {
          setDrafts((prev) => {
            const next = { ...prev };
            delete next[categoryKey];
            return next;
          });
          setSavingCategory(null);
        },
        onError: (err) => {
          setRowErrors((prev) => ({
            ...prev,
            [categoryKey]: getApiErrorMessage(err, 'Failed to save routing'),
          }));
          setSavingCategory(null);
        },
      },
    );
  }

  return (
    <>
      {ROUTING_CATEGORIES.map((cat) => {
        const rows = rowsFor(cat.key);
        const routedIds = new Set(rows.map((r) => r.channel_id));
        const available = allChannels.filter((c) => !routedIds.has(c.id));
        const isDirty = drafts[cat.key] !== undefined;
        const isSaving = savingCategory === cat.key && setRouting.isPending;
        const error = rowErrors[cat.key];

        return (
          <SettingsSection key={cat.key} title={cat.label}>
            <SettingsRowGrid layout="stack">
              <SettingsRow
                name="AssetAtlas (in-app)"
                description="Built-in in-app alerts view — always on for every category"
                control={<span className={styles.alwaysOn}>Always on</span>}
              />

              {rows.map((route) => {
                const name = channelName(route.channel_id);
                return (
                  <SettingsRow
                    key={route.channel_id}
                    name={name}
                    control={
                      <div className={styles.rowControls}>
                        <select
                          className={styles.select}
                          aria-label={`${name} minimum severity`}
                          value={route.min_severity}
                          onChange={(e) =>
                            setSeverity(cat.key, route.channel_id, e.target.value as Severity)
                          }
                        >
                          {SEVERITIES.map((s) => (
                            <option key={s} value={s}>{SEVERITY_LABELS[s]}</option>
                          ))}
                        </select>
                        <IconButton
                          aria-label={`Remove ${name}`}
                          onClick={() => removeChannel(cat.key, route.channel_id)}
                        >
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M18 6L6 18" /><path d="M6 6l12 12" />
                          </svg>
                        </IconButton>
                      </div>
                    }
                  />
                );
              })}

              <SettingsRow
                name="Add channel"
                control={
                  available.length > 0 ? (
                    <select
                      className={styles.select}
                      aria-label={`Add channel to ${cat.label}`}
                      value=""
                      onChange={(e) => addChannel(cat.key, e.target.value)}
                    >
                      {/* disabled+hidden: shows as the collapsed prompt but is
                          absent from the open list, so the OS draws no checkmark
                          on a selected placeholder. */}
                      <option value="" disabled hidden>+ Add channel</option>
                      {available.map((c) => (
                        <option key={c.id} value={c.id}>{c.name}</option>
                      ))}
                    </select>
                  ) : allChannels.length === 0 ? (
                    <span className={styles.hint}>No channels configured yet</span>
                  ) : (
                    <span className={styles.hint}>All channels routed</span>
                  )
                }
              />
            </SettingsRowGrid>

            <div className={styles.actions}>
              {error && <InlineStatus status={{ kind: 'error', message: error }} align="inline" />}
              <Button
                variant={isDirty ? 'primary' : 'secondary'}
                onClick={() => handleSave(cat.key)}
                loading={isSaving}
                disabled={!isDirty}
              >
                Save
              </Button>
            </div>
          </SettingsSection>
        );
      })}
    </>
  );
}
