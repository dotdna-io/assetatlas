// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/MergeDenylistTab.tsx
//
// Admin screen for Settings -> Data -> Merge Denylist (Task 13).
// Lists, adds, and removes identifiers the pipeline must never use to bridge
// two devices into one asset. This is the write side; the read-only per-asset
// "merge keys" viewer is a separate panel on the asset-detail page.
//
// Entries are exact values or patterns (#991): one 'bc:24:11:*' stands in for
// the whole Proxmox guest-veth OUI. Which patterns each identifier type takes
// is MATCH_TYPES_BY_TYPE, mirrored from the encoder; the encoder still has the
// final say on save, and says so through getApiErrorMessage.

import { useEffect, useState } from 'react';
import { SettingsSection } from '@/components/settings';
import { Button } from '@/components/ui/Button';
import { IconButton } from '@/components/ui/IconButton';
import { InlineStatus, successFor, useInlineStatus } from '@/components/ui/InlineStatus';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { getApiErrorMessage } from '@/api/client';
import {
  useMergeDenylist,
  useCreateDenylistEntry,
  useDeleteDenylistEntry,
  useDenylistMatchPreview,
  MATCH_TYPES_BY_TYPE,
  PREVIEW_LIMIT,
  type IdentifierType,
  type MatchType,
  type DenylistEntry,
  type DenylistDraft,
  type DenylistMatchPreview,
} from '@/api/merge-denylist';
import styles from './MergeDenylistTab.module.css';

const TYPES: IdentifierType[] = ['hostname', 'fqdn', 'mac', 'serial'];

const TYPE_LABELS: Record<IdentifierType, string> = {
  hostname: 'Hostname',
  fqdn: 'FQDN',
  mac: 'MAC',
  serial: 'Serial',
};

// 'Starts with' / 'Ends with' rather than 'Prefix' / 'Suffix': the same words
// the Explorer filter row uses for the same idea (operators.py scalar_label).
const MATCH_TYPE_LABELS: Record<MatchType, string> = {
  exact: 'Exact',
  prefix: 'Starts with',
  suffix: 'Ends with',
  range: 'Range',
};

// What the preview is counting — the identifier values on the assets, which is
// what a denylist entry takes away.
const UNIT_LABELS: Record<IdentifierType, string> = {
  hostname: 'hostnames',
  fqdn: 'FQDNs',
  mac: 'MAC addresses',
  serial: 'serial numbers',
};

function valuePlaceholder(type: IdentifierType, matchType: MatchType): string {
  if (matchType === 'prefix') {
    return type === 'mac' ? 'prefix (e.g. bc:24:11:*)' : 'prefix (e.g. web*)';
  }
  if (matchType === 'suffix') return 'suffix (e.g. *.corp.local)';
  return type === 'mac' ? 'identifier (e.g. bc:24:11:22:33:44)' : 'identifier (e.g. web01)';
}

// Share of the fleet's identifiers at which an entry stops being routine. The
// issue's own worked example — a 1-nibble MAC prefix eating ~6% of MAC space —
// sits just above it.
const LOUD_SHARE = 0.05;

// Colour the count when the entry takes out a real share of the fleet. A share
// rather than a raw count: 60 of 70 MACs is the disaster this preview exists to
// catch, and 101 of 76,456 hostnames is noise.
function isPreviewLoud(preview: DenylistMatchPreview): boolean {
  if (preview.matched === 0) return false;
  // Capped means at least PREVIEW_LIMIT matched, which is broad on any fleet.
  return preview.capped || preview.matched >= preview.total * LOUD_SHARE;
}

function previewMessage(draft: DenylistDraft, preview: DenylistMatchPreview): string {
  const unit = UNIT_LABELS[draft.identifier_type];
  const total = preview.total.toLocaleString();
  // Once the fetch is truncated, `matched` is a floor rather than a total. For
  // every match type but range that floor is a fair one, because the fetch was
  // narrowed to things that really match. A range can only be narrowed by the
  // prefix enclosing it, which may be vastly wider than the range itself, so its
  // floor could read as reassuring when the truth is not — say so instead.
  if (preview.capped && (draft.match_type === 'range' || preview.matched === 0)) {
    return `Too many ${unit} to count exactly here — narrow this entry to preview it.`;
  }
  if (preview.matched === 0) return `Matches none of your ${total} ${unit}.`;
  const matched = preview.matched.toLocaleString();
  return preview.capped
    ? `Matches at least ${matched} of your ${total} ${unit}.`
    : `Matches ${matched} of your ${total} ${unit}.`;
}

export function MergeDenylistTab() {
  const { data, isLoading } = useMergeDenylist();
  const [status, setStatus] = useInlineStatus();
  const [type, setType] = useState<IdentifierType>('hostname');
  const [matchType, setMatchType] = useState<MatchType>('exact');
  const [value, setValue] = useState('');
  const [rangeStart, setRangeStart] = useState('');
  const [rangeEnd, setRangeEnd] = useState('');
  const [reason, setReason] = useState('');
  const [pendingDelete, setPendingDelete] = useState<DenylistEntry | null>(null);
  const [draft, setDraft] = useState<DenylistDraft | null>(null);

  const isRange = matchType === 'range';
  const isComplete = isRange
    ? !!rangeStart.trim() && !!rangeEnd.trim()
    : !!value.trim();

  // Debounced so the preview costs one pair of queries per pause, not per
  // keystroke — same 350ms as the unified-field modal's live preview.
  useEffect(() => {
    if (!isComplete) {
      setDraft(null);
      return;
    }
    const id = setTimeout(
      () =>
        setDraft({
          identifier_type: type,
          match_type: matchType,
          raw_value: value.trim(),
          range_start: rangeStart.trim(),
          range_end: rangeEnd.trim(),
        }),
      350,
    );
    return () => clearTimeout(id);
  }, [type, matchType, value, rangeStart, rangeEnd, isComplete]);

  const preview = useDenylistMatchPreview(draft);

  const create = useCreateDenylistEntry({
    onSuccess: () => {
      setValue('');
      setRangeStart('');
      setRangeEnd('');
      setReason('');
      setStatus({ kind: 'success', message: 'Entry added', action: 'add' });
    },
    onError: (e) => setStatus({ kind: 'error', message: getApiErrorMessage(e, 'Add failed') }),
  });

  const remove = useDeleteDenylistEntry({
    onSuccess: () => {
      setPendingDelete(null);
      setStatus(null); // the row leaves the table
    },
    onError: (e) => setStatus({ kind: 'error', message: getApiErrorMessage(e, 'Delete failed') }),
  });

  if (isLoading) return <LoadingSpinner />;
  const entries = data?.entries ?? [];

  function handleTypeChange(next: IdentifierType) {
    setType(next);
    // Switching to serial from a hostname suffix would leave an illegal pair
    // selected, which the API rejects and the encoder cannot spell.
    if (!MATCH_TYPES_BY_TYPE[next].includes(matchType)) setMatchType('exact');
  }

  function handleAdd() {
    if (!isComplete) return;
    const reasonText = reason.trim() || null;
    // Split rather than spread onto a shared object: DenylistCreateBody is a
    // union of the two shapes the API accepts, and narrowing match_type is what
    // picks the arm.
    if (matchType === 'range') {
      create.mutate({
        identifier_type: type,
        match_type: 'range',
        range_start: rangeStart.trim(),
        range_end: rangeEnd.trim(),
        reason: reasonText,
      });
      return;
    }
    create.mutate({
      identifier_type: type,
      match_type: matchType,
      raw_value: value.trim(),
      reason: reasonText,
    });
  }

  return (
    <>
      <SettingsSection
        // Each line is one string expression, not loose JSX text: an entity or a
        // wrapped line splits the sentence into several text nodes, and the
        // warning then matches no single element — which is how the guard test
        // reads it, and how a screen reader would too.
        title={
          <>
            {"Identifiers the pipeline must never use to merge assets — reused hostnames, shared or virtual MAC addresses, ambiguous serials. Denylisting a device's only identifier will drop it from the inventory entirely."}
            <br />
            {'Entries take effect on the next pipeline run.'}
          </>
        }
        descriptive
      >
        <InlineStatus status={status} />

        <div className={styles.addRow}>
          <select
            className={styles.select}
            aria-label="Identifier type"
            value={type}
            onChange={(e) => handleTypeChange(e.target.value as IdentifierType)}
          >
            {TYPES.map((t) => (
              <option key={t} value={t}>{TYPE_LABELS[t]}</option>
            ))}
          </select>
          <select
            className={styles.select}
            aria-label="Match type"
            value={matchType}
            onChange={(e) => setMatchType(e.target.value as MatchType)}
          >
            {MATCH_TYPES_BY_TYPE[type].map((m) => (
              <option key={m} value={m}>{MATCH_TYPE_LABELS[m]}</option>
            ))}
          </select>
          {isRange ? (
            <>
              <input
                className={styles.input}
                aria-label="Range start"
                placeholder="start (e.g. bc:24:11:00:00:00)"
                value={rangeStart}
                onChange={(e) => setRangeStart(e.target.value)}
              />
              <span className={styles.rangeSep}>to</span>
              <input
                className={styles.input}
                aria-label="Range end"
                placeholder="end (e.g. bc:24:11:ff:ff:ff)"
                value={rangeEnd}
                onChange={(e) => setRangeEnd(e.target.value)}
              />
            </>
          ) : (
            <input
              className={styles.input}
              aria-label="Identifier value"
              placeholder={valuePlaceholder(type, matchType)}
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          )}
          <input
            className={styles.input}
            aria-label="Reason"
            placeholder="reason (optional)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <Button
            variant="primary"
            loading={create.isPending}
            disabled={!isComplete}
            onClick={handleAdd}
            success={successFor(status, 'add')}
            successLabel="Added"
          >
            Add
          </Button>
        </div>

        {/* How much of the fleet this entry would take out of identity, before
            it exists. The 6-hex floor on MAC prefixes only catches the most
            extreme mistake; a range can be made arbitrarily wide and no floor
            catches that, so the number is the real guard. */}
        {draft && (preview.isPending || preview.isError || preview.data) && (
          <div className={styles.previewRow} role="status">
            {preview.isPending && <span className={styles.previewMuted}>Checking matches…</span>}
            {!preview.isPending && preview.isError && (
              <span className={styles.previewMuted}>
                {getApiErrorMessage(preview.error, 'Match preview unavailable')}
              </span>
            )}
            {!preview.isPending && !preview.isError && preview.data && (
              <span
                className={isPreviewLoud(preview.data) ? styles.previewLoud : styles.previewMuted}
              >
                {previewMessage(draft, preview.data)}
              </span>
            )}
          </div>
        )}

        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}>Type</th>
                <th className={styles.th}>Match</th>
                <th className={styles.th}>Value</th>
                <th className={styles.th}>Reason</th>
                <th className={styles.th}>Added by</th>
                <th className={`${styles.th} ${styles.thRight}`}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {entries.length === 0 ? (
                <tr>
                  <td className={styles.emptyCell} colSpan={6}>
                    No denylisted identifiers.
                  </td>
                </tr>
              ) : (
                entries.map((entry) => (
                  <tr key={entry.id}>
                    <td className={styles.td}>{TYPE_LABELS[entry.identifier_type]}</td>
                    <td className={`${styles.td} ${styles.secondary}`}>
                      {MATCH_TYPE_LABELS[entry.match_type] ?? entry.match_type}
                    </td>
                    {/* raw_value is what the admin typed, canonicalised for
                        display by the service — including a range, which it
                        spells '<start> - <end>'. */}
                    <td className={styles.td}>{entry.raw_value}</td>
                    <td className={`${styles.td} ${styles.secondary}`}>{entry.reason ?? '—'}</td>
                    <td className={`${styles.td} ${styles.secondary}`}>{entry.created_by}</td>
                    <td className={`${styles.td} ${styles.actionsCell}`}>
                      <IconButton
                        aria-label={`Remove ${entry.raw_value}`}
                        onClick={() => setPendingDelete(entry)}
                      >
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M18 6L6 18" /><path d="M6 6l12 12" />
                        </svg>
                      </IconButton>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </SettingsSection>

      <ConfirmDialog
        isOpen={pendingDelete !== null}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete) remove.mutate(pendingDelete.id);
        }}
        title="Remove denylist entry"
        message={`Remove '${pendingDelete?.raw_value ?? ''}'? The next pipeline run may re-merge assets that this entry was keeping apart.`}
        confirmLabel="Remove"
        variant="danger"
      />
    </>
  );
}
