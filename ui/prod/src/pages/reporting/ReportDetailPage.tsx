// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useCallback, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useDocumentTitle } from '@/hooks';
import { assetUrl } from '@/basePath';
import {
  useReport,
  useCreateReport,
  useUpdateReport,
  useDeleteReport,
  useCloneReport,
} from '@/api/reports';
import { Button } from '@/components/ui/Button';
import { DeleteIconButton } from '@/components/ui/IconButton';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { ChartGrid } from './grid/ChartGrid';
import { ChartWizard } from './wizard/ChartWizard';
import { ChartEditor } from './editor/ChartEditor';
import { apiClient } from '@/api/client';
import type { FieldCatalogEntry, FieldId } from '@/types/field-id';
import type { ChartConfig } from './grid/ChartCard';
import styles from './ReportDetailPage.module.css';

// Mirror QuestionRenderer.AssetField. The `value` string is the encoded_key
// used by <option value=>, `field_id` is the typed discriminated union.
interface AssetField {
  name: string;
  value: string;
  field_id: FieldId;
  type: string;
  category: string;
}

// Map a catalog entry's `category` (wire form) to the UI section label used
// by QuestionRenderer's grouped <optgroup>.
function categoryLabel(c: FieldCatalogEntry['category']): string {
  switch (c) {
    case 'target':
    case 'advanced':
    case 'virtual':
    case 'unified':
      return 'Asset Properties';
    case 'enrichment':
      return 'Enrichment';
    case 'extended_attributes':
      return 'Extended Attributes';
    case 'tags':
      return 'Tags';
    case 'posture':
      return 'Posture';
    default:
      return 'Other';
  }
}

export default function ReportDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [status, setStatus] = useInlineStatus();
  const isNew = !id || id === 'new';

  const { data: report, isLoading } = useReport(isNew ? undefined : id);

  useDocumentTitle(isNew ? 'New Report' : (report?.title ?? 'Report'));

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [charts, setCharts] = useState<Record<string, any>[]>([]);
  const [isDirty, setIsDirty] = useState(false);
  const [isEditing, setIsEditing] = useState(isNew);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [editorIndex, setEditorIndex] = useState<number | null>(null);
  const [assetFields, setAssetFields] = useState<AssetField[]>([]);
  const initialized = useRef(false);

  useEffect(() => {
    if (report && !initialized.current) {
      setTitle(report.title);
      setDescription(report.description || '');
      setCharts(report.charts);
      initialized.current = true;
    }
  }, [report]);

  // Load catalog entries from /tag-rules/fields. The response is now a flat
  // { entries: FieldCatalogEntry[] } shape (see build_catalog_response); the
  // former sub-arrays (target_fields, extended_fields, tag_fields) are gone.
  useEffect(() => {
    apiClient.get('/tag-rules/fields').then((resp: any) => {
      const data = resp?.data ?? resp;
      const entries: FieldCatalogEntry[] = data?.entries ?? [];
      const fields: AssetField[] = entries.map((e) => ({
        name: e.display_name,
        value: e.encoded_key,
        field_id: e.field_id,
        type: e.data_type,
        category: categoryLabel(e.category),
      }));
      setAssetFields(fields);
    }).catch(() => {});
  }, []);

  const updateField = useCallback(
    <T,>(setter: React.Dispatch<React.SetStateAction<T>>) =>
      (value: T) => {
        setter(value);
        setIsDirty(true);
      },
    [],
  );

  const createMutation = useCreateReport({
    onSuccess: (newReport) => {
      // No success line — the report itself appears, and updating a report
      // already says nothing. Cleared rather than left, so the "Title is
      // required" error from a rejected attempt does not sit above the
      // report that has just been created.
      setStatus(null);
      setIsDirty(false);
      navigate(`/reporting/${newReport.id}`, { replace: true });
    },
  });

  const updateMutation = useUpdateReport(id || '', {
    onSuccess: () => {
      setIsDirty(false);
      setIsEditing(false);
    },
  });

  const deleteMutation = useDeleteReport({
    onSuccess: () => {
      setStatus({ kind: 'success', message: 'Report deleted' });
      navigate('/reporting');
    },
  });

  const cloneMutation = useCloneReport({
    onSuccess: (cloned) => {
      setStatus({ kind: 'success', message: 'Report cloned' });
      window.open(assetUrl(`/reporting/${cloned.id}`), '_blank');
    },
  });

  const handleSave = () => {
    if (!title.trim()) {
      setStatus({ kind: 'error', message: 'Title is required' });
      return;
    }
    if (isNew) {
      createMutation.mutate({ title: title.trim(), description: description.trim() || undefined, charts });
    } else {
      updateMutation.mutate({ title: title.trim(), description: description.trim() || undefined, charts });
    }
  };

  const isSaving = createMutation.isPending || updateMutation.isPending;

  if (!isNew && isLoading) {
    return (
      <div className={styles.loadingWrap}>
        <LoadingSpinner />
      </div>
    );
  }

  return (
    <div>
      <InlineStatus status={status} />
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <input
            className={styles.titleInput}
            value={title}
            onChange={(e) => updateField(setTitle)(e.target.value)}
            placeholder="Report title"
            readOnly={!isEditing}
          />
          <textarea
            className={styles.descInput}
            value={description}
            onChange={(e) => updateField(setDescription)(e.target.value)}
            placeholder="Description (optional)"
            rows={1}
            readOnly={!isEditing}
          />
        </div>
        <div className={styles.headerRight}>
          {!isNew && (
            <>
              <button
                className={styles.iconBtn}
                onClick={() => cloneMutation.mutate(id!)}
                disabled={cloneMutation.isPending}
                aria-label="Clone report"
                title="Clone"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                </svg>
              </button>
              <DeleteIconButton
                className={styles.iconBtn}
                onClick={() => setShowDeleteConfirm(true)}
                aria-label="Delete report"
                title="Delete"
                size={18}
              />
            </>
          )}
          {isEditing ? (
            <div className={`${styles.saveBtn} ${isDirty ? styles.visible : ''}`}>
              <Button
                variant="secondary"
                onClick={handleSave}
                loading={isSaving}
              >
                {/* "Save" in both states. The button used to read "Create"
                    until the report was first persisted, so the same control
                    in the same place renamed itself mid-flow — and it did so
                    during the navigation to the new id, where nobody sees it.
                    "This does not exist yet" is already carried by the button
                    only appearing while there are uncommitted changes. */}
                Save
              </Button>
            </div>
          ) : (
            <Button variant="secondary" onClick={() => setIsEditing(true)}>
              Edit
            </Button>
          )}
          {isEditing && (
            <Button variant="primary" onClick={() => setWizardOpen(true)}>
              Add Chart
            </Button>
          )}
        </div>
      </div>

      {charts.length === 0 ? (
        <div className={styles.emptyState}>
          <p>This report has no charts yet</p>
        </div>
      ) : (
        <ChartGrid
          charts={charts as ChartConfig[]}
          onChartsChange={(updated) => { setCharts(updated); setIsDirty(true); }}
          onConfigureChart={(idx) => setEditorIndex(idx)}
          readOnly={!isEditing}
        />
      )}

      <ChartWizard
        isOpen={wizardOpen}
        onClose={() => setWizardOpen(false)}
        charts={charts as ChartConfig[]}
        onAddChart={(newChart) => {
          setCharts((prev) => [...prev, newChart]);
          setIsDirty(true);
          setTimeout(() => setEditorIndex(charts.length), 100);
        }}
      />

      <ChartEditor
        isOpen={editorIndex !== null}
        onClose={() => setEditorIndex(null)}
        chart={editorIndex !== null ? (charts[editorIndex] as ChartConfig) : null}
        assetFields={assetFields}
        onSave={(queryConfig, name) => {
          if (editorIndex === null) return;
          const updated = [...charts];
          updated[editorIndex] = { ...updated[editorIndex], queryConfig, name };
          setCharts(updated);
          setIsDirty(true);
        }}
      />

      <ConfirmDialog
        isOpen={showDeleteConfirm}
        onCancel={() => setShowDeleteConfirm(false)}
        onConfirm={() => deleteMutation.mutate(id!)}
        title="Delete Report"
        message={`Are you sure you want to delete "${title}"? This cannot be undone.`}
        confirmLabel="Delete"
        variant="danger"
      />
    </div>
  );
}
