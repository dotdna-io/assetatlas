// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/enrichment/UploadModal.tsx
import { useState, useRef, useCallback, useMemo } from 'react';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { useUploadEnrichmentDataset, useApplyEnrichmentDataset } from '@/api/settings';
import { useTagRuleFields } from '@/api/tag-rules';
import type { FieldId } from '@/types/field-id';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { SearchableDropdown, type DropdownOption } from '@/components/ui/SearchableDropdown';
import styles from './UploadModal.module.css';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Mirror of FilterRow's grouping so the same CategoryLabels appear in
// the same order (Asset Properties first, Data Source Specific next, etc.).
const CATEGORY_LABELS: Record<string, string> = {
  target: 'Asset Properties',
  advanced: 'Advanced',
  virtual: 'Computed',
  extended_attributes: 'Data Source Specific',
  tags: 'Tags',
  enrichment: 'Enrichment',
  posture: 'Posture',
};

const CATEGORY_ORDER = [
  'target', 'advanced', 'virtual', 'extended_attributes', 'tags', 'enrichment', 'posture',
] as const;

const FIELD_GROUP_ORDER = CATEGORY_ORDER.map((c) => CATEGORY_LABELS[c]);

// Catalog categories that make sense as enrichment match keys: core asset
// fields (target/advanced), AssetAtlas enrichment fields, and data-source-
// specific fields incl. integration groups (extended_attributes). Tags,
// posture, and computed (virtual) fields are intentionally excluded — matching
// a CSV key against a classification or computed value is not meaningful.
const MATCHABLE_CATEGORIES = new Set([
  'target',
  'advanced',
  'enrichment',
  'extended_attributes',
]);

/** CIDR matching is only valid against the core IP Address column. */
function isIpFieldId(fid: FieldId | undefined): boolean {
  return !!fid && fid.kind === 'static' && fid.name === 'IP Address';
}

// ---------------------------------------------------------------------------
// CSV header parser (handles quoted fields)
// ---------------------------------------------------------------------------

function parseCSVLine(line: string): string[] {
  const headers: string[] = [];
  let current = '';
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      inQuotes = !inQuotes;
    } else if (ch === ',' && !inQuotes) {
      headers.push(current.trim());
      current = '';
    } else {
      current += ch;
    }
  }
  headers.push(current.trim());
  return headers.filter((h) => h.length > 0);
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface UploadModalProps {
  isOpen: boolean;
  onClose: () => void;
}

interface UploadResult {
  dataset_id?: string;
  row_count?: number;
  preview?: Record<string, any>[];
  columns?: string[];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function UploadModal({ isOpen, onClose }: UploadModalProps) {
  const [status, setStatus] = useInlineStatus();
  const { data: fieldsData } = useTagRuleFields();
  const uploadDataset = useUploadEnrichmentDataset();
  const applyDataset = useApplyEnrichmentDataset();

  // Form state
  const [name, setName] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [matchCol, setMatchCol] = useState('');
  const [enrichCol, setEnrichCol] = useState('');
  // matchField holds the catalog's canonical encoded field id (e.g.
  // "static:Hostname", "group:Entra:building") — exactly the value the
  // SearchableDropdown uses and the value the backend match_field validator
  // now accepts. No lossy translation step.
  const [matchField, setMatchField] = useState('');
  const [matchType, setMatchType] = useState<'exact' | 'cidr'>('exact');
  const [targetField, setTargetField] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState('');

  // Preview state (post-upload)
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [uploadedName, setUploadedName] = useState('');

  const fileInputRef = useRef<HTMLInputElement>(null);

  // ---------------------------------------------------------------------------
  // Match-field options — sourced from the same /tag-rules/fields catalog
  // the rules wizard's FilterRow uses, filtered to the categories that make
  // sense as enrichment match keys (core + data-source fields). The user can
  // match on any of those, including integration-group attributes for CIDR/
  // exact matching against an external CSV.
  // ---------------------------------------------------------------------------

  const catalogEntries = fieldsData?.entries ?? [];

  const matchFieldOptions: DropdownOption[] = useMemo(
    () =>
      catalogEntries
        .filter((e) => MATCHABLE_CATEGORIES.has(e.category))
        .map((e) => ({
          value: e.encoded_key,
          label: e.display_name,
          group: CATEGORY_LABELS[e.category] ?? e.category ?? 'Other',
        })),
    [catalogEntries],
  );

  // Whether the picked field is the IP Address column — gates CIDR matching.
  const selectedIsIp = useMemo(
    () => isIpFieldId(catalogEntries.find((e) => e.encoded_key === matchField)?.field_id),
    [catalogEntries, matchField],
  );

  // ---------------------------------------------------------------------------
  // File processing
  // ---------------------------------------------------------------------------

  function processFile(f: File) {
    if (!f.name.toLowerCase().endsWith('.csv')) {
      setError('Please select a CSV file.');
      return;
    }
    setFile(f);
    setError('');

    const reader = new FileReader();
    reader.onload = (e) => {
      const text = (e.target?.result as string) ?? '';
      const firstLine = text.split(/\r?\n/)[0];
      if (!firstLine) {
        setError('CSV file appears to be empty.');
        setHeaders([]);
        return;
      }
      const parsed = parseCSVLine(firstLine);
      if (parsed.length < 2) {
        setError('CSV must have at least 2 columns.');
        setHeaders([]);
        return;
      }
      setHeaders(parsed);
      setMatchCol(parsed[0]);
      setEnrichCol(parsed[1]);
      // Auto-derive target field from enrich column name
      setTargetField(parsed[1].toLowerCase().replace(/\s+/g, '_'));
    };
    reader.readAsText(f.slice(0, 4096));
  }

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) processFile(f);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) processFile(f);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => setDragOver(false), []);

  // ---------------------------------------------------------------------------
  // Match field → match type gating
  // ---------------------------------------------------------------------------

  function handleMatchFieldChange(encoded: string) {
    setMatchField(encoded);
    const fid = catalogEntries.find((e) => e.encoded_key === encoded)?.field_id;
    if (!isIpFieldId(fid)) {
      setMatchType('exact');
    }
  }

  // ---------------------------------------------------------------------------
  // Upload
  // ---------------------------------------------------------------------------

  function validate(): string | null {
    if (!name.trim()) return 'Dataset name is required.';
    if (!file) return 'Please select a CSV file.';
    if (!matchCol) return 'Please select a match column from the CSV.';
    if (!enrichCol) return 'Please select an enrich column from the CSV.';
    if (!matchField) return 'Please select a match asset field.';
    if (!targetField.trim()) return 'Target field name is required.';
    return null;
  }

  function handleUpload() {
    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }

    setError('');
    const formData = new FormData();
    formData.append('file', file!);
    formData.append('name', name.trim());
    formData.append('match_field', matchField);
    formData.append('match_type', matchType);
    formData.append('target_field', targetField.trim());
    formData.append('match_col', matchCol);
    formData.append('enrich_col', enrichCol);

    uploadDataset.mutate(formData, {
      onSuccess: (result) => {
        const data: UploadResult = result?.data ?? result;
        setUploadResult(data);
        setUploadedName(name.trim());
      },
      onError: (err) => {
        setError(err.message || 'Upload failed. Please try again.');
      },
    });
  }

  function handleApplyNow() {
    if (!uploadResult?.dataset_id) return;
    applyDataset.mutate(uploadResult.dataset_id, {
      onSuccess: (result) => {
        const matched = result?.matched_count ?? result?.data?.matched_count ?? 0;
        setStatus({ kind: 'success', message: `Applied: ${matched} asset${matched !== 1 ? 's' : ''} matched` });
        onClose();
      },
      onError: (err) => {
        setStatus({ kind: 'error', message: err.message || 'Apply failed' });
      },
    });
  }

  function handleClose() {
    // Reset state
    setName('');
    setFile(null);
    setHeaders([]);
    setMatchCol('');
    setEnrichCol('');
    setMatchField('');
    setMatchType('exact');
    setTargetField('');
    setError('');
    setUploadResult(null);
    setUploadedName('');
    onClose();
  }

  // ---------------------------------------------------------------------------
  // Render: preview (post-upload)
  // ---------------------------------------------------------------------------

  if (uploadResult) {
    const preview = uploadResult.preview ?? [];
    const columns = uploadResult.columns ?? [];
    const rowCount = uploadResult.row_count ?? 0;
    const displayCols = columns.slice(0, 6);

    return (
      <Modal isOpen={isOpen} onClose={handleClose} title="Upload Successful" size="lg">
        <InlineStatus status={status} />
        <div className={styles.successBanner}>
          <div className={styles.successTitle}>Dataset '{uploadedName}' created</div>
          <div className={styles.successBody}>
            {rowCount} row{rowCount !== 1 ? 's' : ''} stored. Use <strong>Apply</strong> to match against assets.
          </div>
        </div>

        {preview.length > 0 && displayCols.length > 0 && (
          <>
            <div className={styles.previewLabel}>
              Preview (first {Math.min(preview.length, 20)} of {rowCount} rows)
            </div>
            <div className={styles.previewWrapper}>
              <table className={styles.previewTable}>
                <thead>
                  <tr>
                    {displayCols.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.slice(0, 20).map((row, i) => (
                    <tr key={i}>
                      {displayCols.map((c) => (
                        <td key={c}>{row[c] ?? ''}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        <div className={styles.footer}>
          <Button variant="secondary" onClick={handleClose}>
            Close
          </Button>
          {uploadResult.dataset_id && (
            <Button onClick={handleApplyNow} loading={applyDataset.isPending}>
              Apply Now
            </Button>
          )}
        </div>
      </Modal>
    );
  }

  // ---------------------------------------------------------------------------
  // Render: upload form
  // ---------------------------------------------------------------------------

  return (
    <Modal isOpen={isOpen} onClose={handleClose} title="Upload Enrichment CSV" size="md">
      <InlineStatus status={status} />
      <div className={styles.form}>
        {/* Dataset name */}
        <div className={styles.field}>
          <label className={styles.label}>Dataset Name</label>
          <input
            className={styles.input}
            type="text"
            placeholder="e.g. Location Mapping"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        {/* File picker */}
        <div className={styles.field}>
          <label className={styles.label}>CSV File</label>
          <div
            className={[styles.dropZone, dragOver ? styles.dropZoneActive : ''].filter(Boolean).join(' ')}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onClick={() => fileInputRef.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') fileInputRef.current?.click(); }}
          >
            {file ? (
              <span className={styles.fileName}>
                <strong>{file.name}</strong>{' '}
                <span className={styles.fileSize}>({(file.size / 1024).toFixed(1)} KB)</span>
              </span>
            ) : (
              <span className={styles.dropHint}>Drag &amp; drop a CSV file here, or click to browse</span>
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv"
              style={{ display: 'none' }}
              onChange={handleFileChange}
            />
          </div>
        </div>

        {/* Column mapping (shown after file selected) */}
        {headers.length >= 2 && (
          <div className={styles.columnGrid}>
            <div className={styles.field}>
              <label className={styles.label}>Match Column (CSV)</label>
              <select
                className={styles.select}
                value={matchCol}
                onChange={(e) => setMatchCol(e.target.value)}
              >
                {headers.map((h) => (
                  <option key={h} value={h}>{h}</option>
                ))}
              </select>
            </div>
            <div className={styles.field}>
              <label className={styles.label}>Enrich Column (CSV)</label>
              <select
                className={styles.select}
                value={enrichCol}
                onChange={(e) => {
                  setEnrichCol(e.target.value);
                  setTargetField(e.target.value.toLowerCase().replace(/\s+/g, '_'));
                }}
              >
                {headers.map((h) => (
                  <option key={h} value={h}>{h}</option>
                ))}
              </select>
            </div>
          </div>
        )}

        {/* Match asset field \u2014 uses the same SearchableDropdown the
            rules wizard's FilterRow uses, so the field-picker UX is
            consistent across the app. */}
        <div className={styles.field}>
          <label className={styles.label}>Match Asset Field</label>
          <SearchableDropdown
            value={matchField}
            options={matchFieldOptions}
            groupOrder={FIELD_GROUP_ORDER}
            onChange={handleMatchFieldChange}
            ariaLabel="Match asset field"
            placeholder={"Select field…"}
            searchPlaceholder={"Search fields…"}
          />
        </div>

        {/* Match type */}
        <div className={styles.field}>
          <label className={styles.label}>Match Type</label>
          <div className={styles.radioGroup}>
            <label className={styles.radioLabel}>
              <input
                type="radio"
                name="match-type"
                value="exact"
                checked={matchType === 'exact'}
                onChange={() => setMatchType('exact')}
              />
              Exact
            </label>
            <label className={[styles.radioLabel, !selectedIsIp ? styles.radioDisabled : ''].filter(Boolean).join(' ')}>
              <input
                type="radio"
                name="match-type"
                value="cidr"
                checked={matchType === 'cidr'}
                disabled={!selectedIsIp}
                onChange={() => setMatchType('cidr')}
              />
              CIDR
            </label>
          </div>
        </div>

        {/* Target field */}
        <div className={styles.field}>
          <label className={styles.label}>Target Field Name</label>
          <input
            className={styles.input}
            type="text"
            placeholder="e.g. location"
            value={targetField}
            onChange={(e) => setTargetField(e.target.value)}
          />
          <p className={styles.helpText}>
            This will be added to each matching asset's enrichment data.
          </p>
        </div>

        {/* Error */}
        {error && <div className={styles.error}>{error}</div>}

        {/* Buttons */}
        <div className={styles.footer}>
          <Button variant="secondary" onClick={handleClose}>
            Cancel
          </Button>
          <Button onClick={handleUpload} loading={uploadDataset.isPending}>
            {uploadDataset.isPending ? 'Uploading...' : 'Upload & Preview'}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
