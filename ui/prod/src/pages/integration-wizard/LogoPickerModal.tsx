// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useMemo, useRef, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { useApiQuery } from '@/api/hooks';
import { apiClient, getApiErrorMessage } from '@/api/client';
import { resolveLogoUrl } from '@/lib/logoUrl';
import styles from './LogoPickerModal.module.css';

const HIDDEN_LOGOS = new Set(['asset-atlas-symbol.png']);

const ACCEPTED_MIME = ['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml'];
const ACCEPTED_EXT_RE = /\.(png|jpe?g|webp|svg)$/i;
const MAX_BYTES = 1024 * 1024;

interface LogoItem {
  filename: string;
  path: string;
  source: 'library' | 'uploaded';
}

interface LogosResponse {
  success: boolean;
  logos: LogoItem[];
}

interface UploadResponse {
  success: boolean;
  logo: LogoItem;
}

interface LogoPickerModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSelect: (logoPath: string | null) => void;
  selectedLogo: string | null;
}

type Tab = 'library' | 'upload';

function formatLogoName(filename: string): string {
  // Hash-only uploaded filenames don't have a meaningful base name; show
  // a short prefix so the user can tell two uploads apart in the picker.
  if (/^[0-9a-f]{32,}\./.test(filename)) {
    return `Custom · ${filename.slice(0, 6)}`;
  }
  return filename
    .replace(/\.(svg|png|webp|jpg|jpeg|gif)$/i, '')
    .replace(/[-_]/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase());
}

function validateFile(file: File): string | null {
  if (file.size > MAX_BYTES) {
    return `File is too large (${Math.round(file.size / 1024)} KB). Limit is 1 MB.`;
  }
  if (!ACCEPTED_EXT_RE.test(file.name)) {
    return 'Unsupported file type. Allowed: PNG, JPG, WEBP, SVG.';
  }
  // Browsers occasionally report empty mime for SVG; accept anything that
  // already passed the extension check above when type is missing.
  if (file.type && !ACCEPTED_MIME.includes(file.type)) {
    return `Unsupported MIME type "${file.type}". Allowed: PNG, JPG, WEBP, SVG.`;
  }
  return null;
}

export function LogoPickerModal({
  isOpen,
  onClose,
  onSelect,
  selectedLogo,
}: LogoPickerModalProps) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>('library');
  const [search, setSearch] = useState('');
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [pendingPreview, setPendingPreview] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data } = useApiQuery<LogosResponse>(
    ['integrations', 'logos'],
    '/integrations/logos',
    { staleTime: 5 * 60 * 1000 },
  );

  // Revoke any in-flight blob URL on unmount. Without this the user can
  // close the modal mid-flow (Esc, backdrop click, parent unmount) while
  // a preview is showing and we'd leak the object URL until reload.
  useEffect(() => {
    return () => {
      if (pendingPreview) URL.revokeObjectURL(pendingPreview);
    };
  }, [pendingPreview]);

  const allLogos = useMemo(
    () => (data?.logos ?? []).filter(l => !HIDDEN_LOGOS.has(l.filename)),
    [data],
  );

  const filtered = useMemo(() => {
    if (!search.trim()) return allLogos;
    const q = search.toLowerCase();
    return allLogos.filter(l =>
      formatLogoName(l.filename).toLowerCase().includes(q) ||
      l.filename.toLowerCase().includes(q)
    );
  }, [allLogos, search]);

  function clearPendingFile() {
    setPendingFile(null);
    if (pendingPreview) URL.revokeObjectURL(pendingPreview);
    setPendingPreview(null);
    setUploadError(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  function handleFile(file: File | null) {
    setUploadError(null);
    if (!file) {
      clearPendingFile();
      return;
    }
    const err = validateFile(file);
    if (err) {
      setUploadError(err);
      setPendingFile(null);
      if (pendingPreview) URL.revokeObjectURL(pendingPreview);
      setPendingPreview(null);
      return;
    }
    if (pendingPreview) URL.revokeObjectURL(pendingPreview);
    setPendingFile(file);
    setPendingPreview(URL.createObjectURL(file));
  }

  async function handleUpload() {
    if (!pendingFile) return;
    setIsUploading(true);
    setUploadError(null);
    const form = new FormData();
    form.append('file', pendingFile);
    try {
      const res = await apiClient.upload<UploadResponse>(
        '/integrations/logos/upload',
        form,
      );
      // Refetch the listing so the new logo appears in the library tab.
      await queryClient.invalidateQueries({
        queryKey: ['integrations', 'logos'],
      });
      // Auto-select the new logo and close the modal.
      onSelect(res.logo.path);
      clearPendingFile();
      setTab('library');
      onClose();
    } catch (err) {
      setUploadError(getApiErrorMessage(err, 'Upload failed.'));
    } finally {
      setIsUploading(false);
    }
  }

  function handleSelect(path: string) {
    onSelect(path);
    onClose();
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    const file = e.dataTransfer.files?.[0] ?? null;
    handleFile(file);
  }

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Choose a Logo" size="lg">
      <div className={styles.tabs}>
        <button
          type="button"
          className={`${styles.tab} ${tab === 'library' ? styles.tabActive : ''}`}
          onClick={() => setTab('library')}
        >
          Library
        </button>
        <button
          type="button"
          className={`${styles.tab} ${tab === 'upload' ? styles.tabActive : ''}`}
          onClick={() => setTab('upload')}
        >
          Upload custom
        </button>
      </div>

      {tab === 'library' ? (
        <div className={styles.tabPanel}>
          <div className={styles.header}>
            <div className={styles.searchWrap}>
              <input
                type="text"
                className={styles.search}
                placeholder="Search logos..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              {search && (
                <button
                  type="button"
                  className={styles.searchClear}
                  aria-label="Clear search"
                  onClick={() => setSearch('')}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>
                </button>
              )}
            </div>
          </div>
          <div className={styles.count}>{filtered.length} logos available</div>
          {filtered.length === 0 ? (
            <div className={styles.empty}>No logos match your search</div>
          ) : (
            <div className={styles.grid}>
              {filtered.map((logo) => {
                const name = formatLogoName(logo.filename);
                const isSelected =
                  selectedLogo === logo.path ||
                  selectedLogo === logo.filename ||
                  // Bundled compatibility: a stored bare filename matches
                  // the corresponding library entry's path.
                  (logo.source === 'library' &&
                    selectedLogo === `/logos/${logo.filename}`);
                return (
                  <button
                    key={`${logo.source}:${logo.filename}`}
                    type="button"
                    className={`${styles.logoItem} ${isSelected ? styles.logoItemSelected : ''}`}
                    onClick={() => handleSelect(logo.path)}
                    title={
                      logo.source === 'uploaded'
                        ? 'Custom logo (uploaded)'
                        : undefined
                    }
                  >
                    <img
                      src={resolveLogoUrl(logo.path) ?? ''}
                      alt={name}
                      className={styles.logoImg}
                    />
                    <span className={styles.logoName}>{name}</span>
                    {logo.source === 'uploaded' && (
                      <span className={styles.uploadedBadge}>Custom</span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      ) : (
        <div className={styles.tabPanel}>
          <div
            className={styles.dropZone}
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleDrop}
            role="presentation"
          >
            {pendingPreview ? (
              <>
                <img
                  src={pendingPreview}
                  alt="Preview"
                  className={styles.dropPreview}
                />
                <div className={styles.dropMeta}>
                  <strong>{pendingFile?.name}</strong>
                  <span className={styles.dropMetaSub}>
                    {pendingFile && Math.round(pendingFile.size / 1024)} KB
                  </span>
                </div>
              </>
            ) : (
              <>
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className={styles.dropIcon}>
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                  <polyline points="17 8 12 3 7 8"/>
                  <line x1="12" y1="3" x2="12" y2="15"/>
                </svg>
                <div className={styles.dropPrimary}>
                  Drop a logo here or click to browse
                </div>
                <div className={styles.dropHint}>
                  PNG, JPG, WEBP, or SVG · up to 1 MB
                </div>
              </>
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/svg+xml"
              className={styles.fileInput}
              onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
              aria-label="Upload custom logo"
            />
          </div>

          {uploadError && (
            <div className={styles.error} role="alert">{uploadError}</div>
          )}

          <div className={styles.uploadHelp}>
            Uploaded logos are normalized to a 256×256 transparent canvas
            and stripped of metadata. SVGs are sanitized to remove scripts
            and external references before storage.
          </div>

          <div className={styles.uploadActions}>
            {pendingFile && (
              <Button
                variant="ghost"
                onClick={clearPendingFile}
                disabled={isUploading}
              >
                Remove
              </Button>
            )}
            <Button
              variant="primary"
              onClick={handleUpload}
              disabled={!pendingFile || isUploading}
            >
              {isUploading ? 'Uploading…' : 'Upload & use'}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
