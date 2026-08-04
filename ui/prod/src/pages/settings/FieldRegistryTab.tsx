// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/FieldRegistryTab.tsx
import { useState, useEffect, useDeferredValue, useMemo } from 'react';
import { useFieldRegistry, useRegenerateField, useRegenerateSynonyms,
  useSynonymsRegenStatus,
  useFieldDescriptionsConfig, useSetFieldDescriptionsEnabled } from '@/api/settings';
import type { FieldRegistryEntry } from '@/api/settings';
import { SearchInput } from '@/components/ui/SearchInput';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { InlineStatus, successFor, useInlineStatus } from '@/components/ui/InlineStatus';
import type { InlineStatusValue } from '@/components/ui/InlineStatus';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch/ToggleSwitch';
import { EditFieldModal } from './field-registry/EditFieldModal';
import { SynonymsModal } from './field-registry/SynonymsModal';
import styles from './FieldRegistryTab.module.css';

// Shown as the tooltip on both regenerate actions when no LLM provider is
// available (they need one to run).
const NO_PROVIDER_HINT = 'Configure a chatbot LLM provider to regenerate';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

interface FieldGroup {
  key: string;
  label: string;
  fields: FieldRegistryEntry[];
}

function buildGroups(fields: FieldRegistryEntry[]): FieldGroup[] {
  const core: FieldRegistryEntry[] = [];
  const bySource: Record<string, FieldRegistryEntry[]> = {};

  for (const f of fields) {
    if (f.field_type === 'static') {
      core.push(f);
    } else {
      const src = f.data_source || 'Other';
      (bySource[src] = bySource[src] || []).push(f);
    }
  }

  const sorter = (a: FieldRegistryEntry, b: FieldRegistryEntry) =>
    (a.field_name || '').localeCompare(b.field_name || '');

  core.sort(sorter);

  const groups: FieldGroup[] = [];
  if (core.length) {
    groups.push({ key: '__core__', label: 'AssetAtlas Core', fields: core });
  }
  for (const src of Object.keys(bySource).sort()) {
    bySource[src].sort(sorter);
    groups.push({ key: src, label: src, fields: bySource[src] });
  }
  return groups;
}

function filterFields(fields: FieldRegistryEntry[], query: string): FieldRegistryEntry[] {
  if (!query) return fields;
  const q = query.toLowerCase();
  return fields.filter(
    (f) =>
      (f.field_name || '').toLowerCase().includes(q) ||
      (f.display_name || '').toLowerCase().includes(q) ||
      (f.description || '').toLowerCase().includes(q) ||
      (f.data_source || '').toLowerCase().includes(q),
  );
}

function sourceLabel(src: string | null): string {
  const map: Record<string, string> = { hardcoded: 'Built-in', llm: 'AI Generated', manual: 'Manual' };
  return map[src ?? ''] || src || '—';
}

function sourceBadgeStyle(src: string | null): React.CSSProperties {
  if (src === 'llm') return { background: 'rgba(59,130,246,0.12)', color: '#3B82F6' };
  if (src === 'manual') return { background: 'rgba(16,185,129,0.12)', color: '#10B981' };
  return { background: 'rgba(148,163,184,0.12)', color: '#94A3B8' };
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface FieldRowProps {
  field: FieldRegistryEntry;
  onEdit: (f: FieldRegistryEntry) => void;
  onRegen: (fieldName: string) => void;
  onShowSynonyms: (f: FieldRegistryEntry) => void;
  regenPending: boolean;
  regenDisabled: boolean;
  regenSuccess: InlineStatusValue | null;
}

function FieldRow({
  field, onEdit, onRegen, onShowSynonyms, regenPending, regenDisabled, regenSuccess,
}: FieldRowProps) {
  const isCore = field.field_type === 'static';

  return (
    <tr className={styles.tableRow}>
      <td>
        <span className={styles.fieldName}>{field.field_name}</span>
      </td>
      <td>{field.display_name || '—'}</td>
      <td>
        <span className={styles.fieldDescription} title={field.description || ''}>
          {field.description || '—'}
        </span>
      </td>
      <td>
        {field.synonyms && field.synonyms.length > 0 ? (
          <button
            type="button"
            className={styles.synonymCell}
            onClick={() => onShowSynonyms(field)}
            title="View all synonyms"
          >
            <span className={styles.synonymPreview}>
              {field.synonyms.slice(0, 2).join(', ')}
            </span>
            {field.synonyms.length > 2 && (
              <span className={styles.synonymMore}>
                +{field.synonyms.length - 2}
              </span>
            )}
          </button>
        ) : (
          <span className={styles.synonymEmpty}>—</span>
        )}
      </td>
      <td>
        <span className={styles.sourceBadge} style={sourceBadgeStyle(field.description_source)}>
          {sourceLabel(field.description_source)}
        </span>
      </td>
      <td>
        <span
          className={
            field.searchable ? styles.searchableYes : styles.searchableNo
          }
        >
          {field.searchable ? 'Yes' : 'No'}
        </span>
      </td>
      <td>
        <div className={styles.actions}>
          {isCore ? (
            <span className={styles.coreLabel}>Core</span>
          ) : (
            <>
              <Button size="sm" variant="ghost" onClick={() => onEdit(field)}>
                Edit
              </Button>
              <Button
                size="sm"
                variant="ghost"
                loading={regenPending}
                disabled={regenDisabled}
                title={regenDisabled
                  ? NO_PROVIDER_HINT
                  : 'Regenerate description and synonyms'}
                onClick={() => onRegen(field.field_name)}
                success={regenSuccess}
              >
                Regen
              </Button>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}

interface GroupCardProps {
  group: FieldGroup;
  filteredFields: FieldRegistryEntry[];
  isExpanded: boolean;
  onToggle: () => void;
  onEdit: (f: FieldRegistryEntry) => void;
  onRegen: (fieldName: string) => void;
  onShowSynonyms: (f: FieldRegistryEntry) => void;
  regenningField: string | null;
  regenDisabled: boolean;
  /** Resolves the success signal for one row's Regen button. */
  regenSuccessFor: (fieldName: string) => InlineStatusValue | null;
}

function GroupCard({
  group,
  filteredFields,
  isExpanded,
  onToggle,
  onEdit,
  onRegen,
  onShowSynonyms,
  regenningField,
  regenDisabled,
  regenSuccessFor,
}: GroupCardProps) {
  if (filteredFields.length === 0) return null;

  return (
    <div className={styles.group}>
      <div className={styles.groupHeader} onClick={onToggle} role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); } }}>
        <span className={styles.groupTitle}>{group.label}</span>
        <span className={styles.groupCount}>{filteredFields.length}</span>
        <svg
          className={`${styles.chevron}${isExpanded ? ` ${styles.chevronExpanded}` : ''}`}
          width="14"
          height="14"
          viewBox="0 0 14 14"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M5 3l4 4-4 4" />
        </svg>
      </div>
      {isExpanded && (
        <div className={styles.groupContent}>
          <table className={styles.fieldTable}>
            <thead>
              <tr className={styles.tableHeaderRow}>
                <th style={{ width: '160px' }}>Field</th>
                <th style={{ width: '150px' }}>Display Name</th>
                <th>Description</th>
                <th style={{ width: '220px' }}>Synonyms</th>
                <th style={{ width: '110px' }}>Source</th>
                <th style={{ width: '80px' }}>Searchable</th>
                <th style={{ width: '120px' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredFields.map((f) => (
                <FieldRow
                  key={f.field_name}
                  field={f}
                  onEdit={onEdit}
                  onRegen={onRegen}
                  onShowSynonyms={onShowSynonyms}
                  regenPending={regenningField === f.field_name}
                  regenDisabled={regenDisabled}
                  regenSuccess={regenSuccessFor(f.field_name)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function FieldRegistryTab() {
  const { data, isLoading } = useFieldRegistry();
  const [status, setStatus] = useInlineStatus();

  const [searchQuery, setSearchQuery] = useState('');
  const deferredSearch = useDeferredValue(searchQuery);

  // Expanded state: set of group keys that are expanded. Starts empty
  // so the user lands on a collapsed list and chooses what to open —
  // search auto-expands matching groups via isGroupExpanded() below.
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  // Edit modal state
  const [editingField, setEditingField] = useState<FieldRegistryEntry | null>(null);

  // Synonyms modal state (read-only)
  const [synonymsField, setSynonymsField] = useState<FieldRegistryEntry | null>(null);

  // Regen confirm dialogs
  const [regenConfirm, setRegenConfirm] = useState<string | null>(null);
  const [regenSynonymsConfirm, setRegenSynonymsConfirm] = useState(false);

  // The full synonym rebuild runs as a background job; poll while it runs.
  const [synonymsRunning, setSynonymsRunning] = useState(false);
  const [synonymsRunId, setSynonymsRunId] = useState(0);

  // Regen field in progress
  const [regenningField, setRegenningField] = useState<string | null>(null);

  const regenField = useRegenerateField({
    onSuccess: (_data, fieldName) => {
      setStatus({
        kind: 'success',
        message: 'Description and synonyms regenerated',
        action: `regen:${fieldName}`,
      });
      setRegenningField(null);
    },
    onError: (err) => {
      setStatus({ kind: 'error', message: err.message || 'Failed to regenerate' });
      setRegenningField(null);
    },
  });

  const regenSynonyms = useRegenerateSynonyms({
    // 200 => a job is running ('started' or 'already_running'); begin polling.
    // Failures (e.g. 503) surface via onError.
    onSuccess: () => {
      setSynonymsRunId((n) => n + 1);
      setSynonymsRunning(true);
    },
    onError: (err) => setStatus({ kind: 'error',
      message: err.message || 'Failed to start synonym regeneration' }),
  });

  const { data: synStatus, isError: synStatusFailed } =
    useSynonymsRegenStatus(synonymsRunning, synonymsRunId);

  // Detect the running -> done transition and surface the result exactly once.
  // If the status poll keeps failing, stop and surface it rather than spinning
  // forever — the job may still be running in the background.
  useEffect(() => {
    if (!synonymsRunning) return;
    if (synStatusFailed) {
      setSynonymsRunning(false);
      setStatus({ kind: 'error',
        message: 'Could not track synonym regeneration status — it may still be '
          + 'running in the background. Reload the page to check.' });
      return;
    }
    if (synStatus && !synStatus.running) {
      setSynonymsRunning(false);
      const err = synStatus.last_result?.error;
      setStatus(err
        ? { kind: 'error', message: err }
        : { kind: 'success',
            message: synStatus.last_result?.message || 'Synonyms regenerated',
            action: 'regen-all' });
    }
  }, [synonymsRunning, synStatus, synStatusFailed, setStatus]);

  const { data: fdConfig } = useFieldDescriptionsConfig();
  const setAiEnabled = useSetFieldDescriptionsEnabled({
    onSuccess: (r) => setStatus({ kind: 'success',
      message: r.enabled ? 'AI descriptions enabled' : 'AI descriptions disabled' }),
    onError: (err) => setStatus({ kind: 'error',
      message: err.message || 'Failed to update setting' }),
  });
  const providerAvailable = fdConfig?.provider_available ?? false;
  const aiEnabled = fdConfig?.enabled ?? false;
  // #927: on by default when a provider is available; forced off + locked when
  // there's no provider; the admin can still opt out (stored enabled=false).
  const aiEffective = aiEnabled && providerAvailable;

  const fields = data?.fields ?? [];

  const groups = useMemo(() => buildGroups(fields), [fields]);

  // When search is active, filter each group and auto-expand all with matches
  const filteredGroups = useMemo(() => {
    return groups.map((g) => ({
      group: g,
      filtered: filterFields(g.fields, deferredSearch),
    }));
  }, [groups, deferredSearch]);

  const totalVisible = filteredGroups.reduce((sum, { filtered }) => sum + filtered.length, 0);
  const searchable = fields.filter((f) => f.searchable).length;

  function toggleGroup(key: string) {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function isGroupExpanded(key: string): boolean {
    // When searching, auto-expand all groups that have matching fields
    if (deferredSearch) {
      const gData = filteredGroups.find((fg) => fg.group.key === key);
      return (gData?.filtered.length ?? 0) > 0;
    }
    return expandedGroups.has(key);
  }

  function handleEdit(field: FieldRegistryEntry) {
    setEditingField(field);
  }

  function handleRegen(fieldName: string) {
    setRegenConfirm(fieldName);
  }

  function handleRegenSynonyms() {
    setRegenSynonymsConfirm(true);
  }

  if (isLoading) {
    return <LoadingSpinner />;
  }

  return (
    <div>
      <InlineStatus status={status} />
      {/* Intro on the left, Regenerate All in the top right — the action is
          global (regenerates descriptions + synonyms for every extended field
          at once; Core fields are untouched), so it sits at the section header
          rather than buried inside the search toolbar. */}
      <div className={styles.header}>
        <p className={styles.intro}>
          Field descriptions and synonyms drive semantic search in the Explorer.
          Edit or regenerate them to refine match quality.
        </p>
        <div className={styles.aiToggle}>
          <ToggleSwitch
            checked={aiEffective}
            disabled={!providerAvailable || setAiEnabled.isPending}
            onChange={(v) => setAiEnabled.mutate(v)}
            label="Auto-generate descriptions with LLM"
          />
          {!providerAvailable && (
            <span className={styles.aiToggleHint}>
              Configure a chatbot LLM provider to enable AI-generated field descriptions.
            </span>
          )}
        </div>
        <Button
          variant="primary"
          onClick={handleRegenSynonyms}
          loading={regenSynonyms.isPending || synonymsRunning}
          disabled={!providerAvailable || regenSynonyms.isPending || synonymsRunning}
          success={successFor(status, 'regen-all')}
          title={!providerAvailable
            ? NO_PROVIDER_HINT
            : 'Regenerate descriptions and synonyms for every data-source field'}
        >
          Regenerate All
        </Button>
      </div>
      {/* Toolbar — search + stats only */}
      <div className={styles.toolbar}>
        <SearchInput
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder="Search fields…"
          width={320}
        />
        <span className={styles.stats}>
          <strong>{fields.length}</strong> fields
          {' | '}
          <strong>{searchable}</strong> searchable
          {deferredSearch && (
            <>
              {' | '}
              <strong>{totalVisible}</strong> matching
            </>
          )}
        </span>
      </div>

      {/* Groups */}
      {filteredGroups.every(({ filtered }) => filtered.length === 0) ? (
        <div className={styles.empty}>No fields match your filter.</div>
      ) : (
        filteredGroups.map(({ group, filtered }) => (
          <GroupCard
            key={group.key}
            group={group}
            filteredFields={filtered}
            isExpanded={isGroupExpanded(group.key)}
            onToggle={() => toggleGroup(group.key)}
            onEdit={handleEdit}
            onRegen={handleRegen}
            onShowSynonyms={setSynonymsField}
            regenningField={regenningField}
            regenDisabled={!providerAvailable}
            regenSuccessFor={(name) => successFor(status, `regen:${name}`)}
          />
        ))
      )}

      {/* Edit modal */}
      <EditFieldModal
        isOpen={editingField !== null}
        onClose={() => setEditingField(null)}
        field={editingField}
      />

      {/* Synonyms modal (read-only) */}
      <SynonymsModal
        isOpen={synonymsField !== null}
        onClose={() => setSynonymsField(null)}
        field={synonymsField}
      />

      {/* Regen description confirm */}
      <ConfirmDialog
        isOpen={regenConfirm !== null}
        onConfirm={() => {
          if (regenConfirm) {
            setRegenningField(regenConfirm);
            regenField.mutate(regenConfirm);
          }
          setRegenConfirm(null);
        }}
        onCancel={() => setRegenConfirm(null)}
        title="Regenerate Field"
        message={`Regenerate the AI description and synonyms for '${regenConfirm}'?`}
        confirmLabel="Regenerate"
        variant="warning"
      />

      {/* Regen synonyms confirm */}
      <ConfirmDialog
        isOpen={regenSynonymsConfirm}
        onConfirm={() => {
          regenSynonyms.mutate();
          setRegenSynonymsConfirm(false);
        }}
        onCancel={() => setRegenSynonymsConfirm(false)}
        title="Regenerate All"
        message="Regenerate AI descriptions and synonyms for every data-source field? AssetAtlas Core fields are left untouched. This calls the LLM once per field and runs in the background — it can take several minutes on installs with many fields."
        confirmLabel="Regenerate"
        variant="warning"
      />
    </div>
  );
}
