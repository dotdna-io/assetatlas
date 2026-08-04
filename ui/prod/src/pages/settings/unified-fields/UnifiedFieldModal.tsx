// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/unified-fields/UnifiedFieldModal.tsx
//
// Add/Edit modal for a Unified Field: name, type-aware merge function, ordered
// source-field picker, inline validation, live preview, and save.

import { useState, useMemo, useEffect } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { SearchableDropdown, type DropdownOption } from '@/components/ui/SearchableDropdown';
import { IconButton } from '@/components/ui/IconButton';
import { InlineStatus } from '@/components/ui/InlineStatus';
import { getApiErrorMessage } from '@/api/client';
import {
  useUnifiedFields,
  useCreateUnifiedField,
  useUpdateUnifiedField,
  usePreviewUnifiedField,
} from '@/api/unifiedFields';
import type { UnifiedField, UnifiedFieldWriteRequest, UnifiedFieldPreviewRequest } from '@/api/unifiedFields';
import { useExplorerFields } from '@/api/assets';
import { buildFieldMap } from '@/pages/explorer/explorer-utils';
import {
  functionsForSources,
  validateDefinition,
} from './unified-fields-utils';
import type { MergeFunction } from './unified-fields-utils';
import styles from './UnifiedFieldModal.module.css';

// Catalog categories that are NOT valid as unified-field sources. The backend
// rejects built-in virtual fields (confidence_level / asset_label) and unified
// fields themselves, so they must not be offered in the source picker.
const NON_SOURCE_CATEGORIES = new Set(['virtual', 'unified']);

// Format a preview cell. Arrays (union results, multi-value sources) join with
// "; "; objects (jsonb) stringify; everything else via String. Avoids the
// "[object Object]" a bare String() produces on structured values.
function formatPreviewCell(v: unknown): string {
  if (v == null) return '';
  if (Array.isArray(v)) return v.join('; ');
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DraftState {
  name: string;
  function: MergeFunction;
  /** Encoded source keys, in merge order. */
  sources: string[];
}

interface UnifiedFieldModalProps {
  initial?: UnifiedField;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function UnifiedFieldModal({ initial, onClose }: UnifiedFieldModalProps) {
  const title = initial ? 'Edit unified field' : 'Add unified field';

  const [draft, setDraft] = useState<DraftState>(() => ({
    name: initial?.name ?? '',
    function: initial?.function ?? 'coalesce',
    sources: initial?.sources ?? [],
  }));

  // Field catalog for resolving source encoded_keys → data_type
  const { data: fieldsData } = useExplorerFields();
  const fieldMap = useMemo(() => buildFieldMap(fieldsData?.entries), [fieldsData]);

  // Resolve draft sources to { encoded_key, data_type } objects
  const resolvedSources = useMemo(
    () =>
      draft.sources
        .map((key) => {
          const entry = fieldMap[key];
          return entry ? { encoded_key: key, data_type: entry.data_type } : null;
        })
        .filter((x): x is { encoded_key: string; data_type: string } => x !== null),
    [draft.sources, fieldMap],
  );

  // Derive available function options from the resolved sources. This honours
  // the scalar-vs-array distinction (array sources => union only), so the
  // dropdown never offers a function validateDefinition would then reject.
  const functionOptions = useMemo(
    () => functionsForSources(resolvedSources),
    [resolvedSources],
  );

  // When the available options change and the selected function is no longer
  // among them, fall back to the first offered option (always valid). This
  // keeps the controlled <select> showing a value that is actually one of its
  // options — and, for multi-value sources, auto-selects `union` instead of
  // leaving the invalid `coalesce` that would disable Save.
  const effectiveFunction: MergeFunction = useMemo(() => {
    const valid = functionOptions.map((o) => o.value);
    return valid.includes(draft.function) ? draft.function : functionOptions[0]?.value ?? 'coalesce';
  }, [draft.function, functionOptions]);

  // Existing field names for collision detection (exclude the one being edited)
  const { data: unifiedFieldsData } = useUnifiedFields();
  const existingNames = useMemo(
    () =>
      (unifiedFieldsData?.fields ?? [])
        .filter((f) => f.id !== initial?.id)
        .map((f) => f.name.toLowerCase()),
    [unifiedFieldsData, initial],
  );

  // Inline validation
  const errors = useMemo(() => {
    const errs = validateDefinition(
      { name: draft.name, function: effectiveFunction, sources: resolvedSources },
      existingNames,
    );
    // Once the catalog has loaded, flag any selected source that no longer
    // resolves (e.g. its field was deleted). validateDefinition only sees the
    // resolved sources, so without this the form looks valid while the Save
    // payload (draft.sources) still carries a key the backend would reject.
    if (fieldsData && resolvedSources.length !== draft.sources.length) {
      errs.push('Some selected source fields are no longer available — remove them');
    }
    return errs;
  }, [draft.name, effectiveFunction, resolvedSources, existingNames, fieldsData, draft.sources]);

  // Preview mutation — debounced, only fires when draft is valid
  const preview = usePreviewUnifiedField();

  useEffect(() => {
    if (errors.length > 0) return;
    const payload: UnifiedFieldPreviewRequest = {
      function: effectiveFunction,
      sources: draft.sources,
    };
    const id = setTimeout(() => {
      preview.mutate(payload);
    }, 350);
    return () => clearTimeout(id);
    // draft.name is intentionally excluded: it is not part of the preview
    // payload, so typing the name must not re-fire the request. errors.length
    // already captures any name-driven validity change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveFunction, draft.sources, errors.length]);

  // Mutations
  const createField = useCreateUnifiedField();
  const updateField = useUpdateUnifiedField(initial?.id ?? '');

  const isSaving = createField.isPending || updateField.isPending;
  // Surface a failed save (e.g. 409 name collision the client check missed,
  // or a 400/422 from the server) so the backend's message reaches the user.
  const submitError = createField.error ?? updateField.error;

  function handleSave() {
    if (errors.length > 0 || isSaving) return;

    const payload: UnifiedFieldWriteRequest = {
      name: draft.name.trim(),
      function: effectiveFunction,
      sources: draft.sources,
    };

    if (initial) {
      updateField.mutate(payload, { onSuccess: onClose });
    } else {
      createField.mutate(payload, { onSuccess: onClose });
    }
  }

  // Selectable source options: real fields only (exclude built-in virtuals and
  // unified fields — the backend rejects both as sources, #755 D6). Already-
  // selected fields stay in the list; addSource dedupes (matching the prior
  // picker). Search, viewport positioning, and outside-click/Escape dismissal
  // are handled by the shared SearchableDropdown.
  const sourceOptions = useMemo<DropdownOption[]>(
    () =>
      (fieldsData?.entries ?? [])
        .filter((e) => !NON_SOURCE_CATEGORIES.has(e.category))
        .map((e) => ({ value: e.encoded_key, label: e.display_name })),
    [fieldsData],
  );

  function addSource(encodedKey: string) {
    setDraft((d) => {
      if (d.sources.includes(encodedKey)) return d;
      return { ...d, sources: [...d.sources, encodedKey] };
    });
  }

  function removeSource(encodedKey: string) {
    setDraft((d) => ({ ...d, sources: d.sources.filter((k) => k !== encodedKey) }));
  }

  function moveSource(index: number, direction: -1 | 1) {
    setDraft((d) => {
      const next = [...d.sources];
      const swap = index + direction;
      if (swap < 0 || swap >= next.length) return d;
      [next[index], next[swap]] = [next[swap], next[index]];
      return { ...d, sources: next };
    });
  }

  function setName(name: string) {
    setDraft((d) => ({ ...d, name }));
  }

  function setFunction(fn: MergeFunction) {
    setDraft((d) => ({ ...d, function: fn }));
  }

  // The preview table reflects the definition the preview was actually computed
  // for (preview.variables), not the live draft — otherwise, during the debounce
  // after a source change, new column headers would render over stale rows.
  const previewSources = preview.variables?.sources ?? draft.sources;

  return (
    <Modal isOpen title={title} onClose={onClose} size="md" showCloseButton>
      {/* Name field */}
      <div className={styles.field}>
        <label className={styles.label} htmlFor="uf-name">
          Name
        </label>
        <input
          id="uf-name"
          className={styles.input}
          value={draft.name}
          onChange={(e) => setName(e.target.value)}
          autoComplete="off"
        />
      </div>

      {/* Function select */}
      <div className={styles.field}>
        <label className={styles.label} htmlFor="uf-function">
          Function
        </label>
        <select
          id="uf-function"
          className={styles.select}
          value={effectiveFunction}
          onChange={(e) => setFunction(e.target.value as MergeFunction)}
        >
          {functionOptions.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      {/* Source-field picker */}
      <div className={styles.field}>
        <span className={styles.label}>Source fields</span>

        {/* Ordered list of selected sources */}
        {draft.sources.length > 0 && (
          <ul className={styles.sourceList}>
            {draft.sources.map((key, idx) => {
              const entry = fieldMap[key];
              const label = entry?.display_name ?? key;
              return (
                <li key={key} className={styles.sourceItem}>
                  <span className={styles.sourceLabel}>{label}</span>
                  <div className={styles.sourceActions}>
                    <IconButton
                      aria-label="Move up"
                      disabled={idx === 0}
                      onClick={() => moveSource(idx, -1)}
                    >
                      {/* Up chevron */}
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="18 15 12 9 6 15" />
                      </svg>
                    </IconButton>
                    <IconButton
                      aria-label="Move down"
                      disabled={idx === draft.sources.length - 1}
                      onClick={() => moveSource(idx, 1)}
                    >
                      {/* Down chevron */}
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="6 9 12 15 18 9" />
                      </svg>
                    </IconButton>
                    <IconButton
                      aria-label="Remove"
                      onClick={() => removeSource(key)}
                    >
                      {/* X */}
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M18 6L6 18"/><path d="M6 6l12 12"/>
                      </svg>
                    </IconButton>
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {/* Add field — shared SearchableDropdown (search + portal positioning +
            outside-click/Escape dismissal). value="" so the trigger always
            reads "Add field"; each selection is an add, deduped by addSource. */}
        <SearchableDropdown
          value=""
          options={sourceOptions}
          onChange={addSource}
          ariaLabel="Add field"
          placeholder="Add field"
          searchPlaceholder="Search fields…"
        />
      </div>

      {/* Inline validation errors */}
      {errors.length > 0 && (
        <div className={styles.errors} role="alert">
          {errors.map((msg) => (
            <span key={msg} className={styles.error}>
              {msg}
            </span>
          ))}
        </div>
      )}

      {/* Live preview panel — shown when the draft is valid and a preview is
          loading, errored, or has data. */}
      {errors.length === 0 && (preview.isPending || preview.isError || preview.data) && (
        <div className={styles.previewPanel}>
          <span className={styles.previewLabel}>Preview</span>
          {preview.isPending && (
            <div className={styles.previewLoading}>Loading preview...</div>
          )}
          {!preview.isPending && preview.isError && (
            <div className={styles.previewEmpty}>
              {getApiErrorMessage(preview.error, 'Preview failed')}
            </div>
          )}
          {!preview.isPending && !preview.isError && preview.data && preview.data.rows.length === 0 && (
            <div className={styles.previewEmpty}>No sample data</div>
          )}
          {!preview.isPending && !preview.isError && preview.data && preview.data.rows.length > 0 && (
            <div className={styles.previewTableWrap}>
              <table className={styles.previewTable}>
                <thead>
                  <tr>
                    {/* Row identifier: the backend selects virtual:asset_label
                        (COALESCE of override/hostname/fqdn/mac/sn) as row.hostname,
                        NOT the Hostname source field. Label it accordingly so it
                        doesn't read as a duplicate when Hostname is also a source. */}
                    <th className={styles.previewTh}>Asset Label</th>
                    {previewSources.map((key) => (
                      <th key={key} className={styles.previewTh}>
                        {fieldMap[key]?.display_name ?? key}
                      </th>
                    ))}
                    <th className={styles.previewTh}>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.data.rows.map((row) => (
                    <tr key={row.asset_id} className={styles.previewTr}>
                      <td className={styles.previewTd}>{row.hostname}</td>
                      {previewSources.map((key) => (
                        <td key={key} className={styles.previewTd}>
                          {formatPreviewCell(row.source_values[key])}
                        </td>
                      ))}
                      <td className={styles.previewTd}>
                        {formatPreviewCell(row.result)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Submit error (from the server) — distinct from inline validation */}
      <InlineStatus
        status={
          submitError
            ? { kind: 'error', message: getApiErrorMessage(submitError, 'Failed to save unified field') }
            : null
        }
      />

      {/* Footer */}
      <div className={styles.footer}>
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          onClick={handleSave}
          disabled={errors.length > 0}
          loading={isSaving}
        >
          Save
        </Button>
      </div>
    </Modal>
  );
}
