// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useRef, useCallback } from 'react';
import {
  assetsApi,
  useMergeHistory,
  useMergePreview,
  useMergeAssets,
  useUnmergeAsset,
} from '@/api/assets';
import { Button } from '@/components/ui/Button';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import styles from './edit.module.css';

interface MergeTabProps {
  assetId: string;
  assetData?: Record<string, unknown>;
  onSuccess: () => void;
}

interface SearchResult {
  id: string;
  hostname: string | null;
  primary_ip: string | null;
  mac_address: string | null;
  category: string | null;
}

function formatVal(val: unknown, fallback = '-'): string {
  if (Array.isArray(val)) return val.length > 0 ? val.join(', ') : fallback;
  if (val == null || val === '') return fallback;
  return String(val);
}

function formatDate(iso: string | undefined | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function MergeTab({ assetId, assetData, onSuccess }: MergeTabProps) {
  const [status, setStatus] = useInlineStatus();
  const mergeMutation = useMergeAssets();
  const unmergeMutation = useUnmergeAsset();

  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [selectedTarget, setSelectedTarget] = useState<SearchResult | null>(null);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { data: mergeHistoryData, refetch: refetchHistory } = useMergeHistory(assetId);
  const mergeHistory = mergeHistoryData?.merge_history ?? [];

  const { data: previewData } = useMergePreview(
    assetId,
    selectedTarget ? selectedTarget.id : undefined,
  );

  // Debounce search input
  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setSearchQuery(val);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedQuery(val);
    }, 300);
  };

  // Run search when debounced query changes
  const runSearch = useCallback(
    async (q: string) => {
      if (q.length < 2) {
        setSearchResults([]);
        return;
      }
      setSearchLoading(true);
      try {
        const data = await assetsApi.search({ q, limit: 11 });
        const results = (data.results ?? [])
          .filter((r) => r.id !== assetId)
          .slice(0, 10)
          .map((r) => ({
            id: String(r.id),
            hostname: r.hostname,
            primary_ip: r.primary_ip,
            mac_address: r.mac_address,
            category: null,
          }));
        setSearchResults(results);
      } catch {
        setStatus({ kind: 'error', message: 'Search failed' });
        setSearchResults([]);
      } finally {
        setSearchLoading(false);
      }
    },
    [assetId, setStatus],
  );

  useEffect(() => {
    runSearch(debouncedQuery);
  }, [debouncedQuery, runSearch]);

  const handleSelectTarget = (result: SearchResult) => {
    setSelectedTarget((prev) => (prev?.id === result.id ? null : result));
  };

  const handleMerge = () => {
    if (!selectedTarget) return;
    mergeMutation.mutate(
      {
        primary_asset_id: assetId,
        secondary_asset_id: selectedTarget.id,
        merge_strategy: 'prefer_primary',
      },
      {
        onSuccess: () => {
          setStatus({ kind: 'success', message: 'Assets merged successfully' });
          setSelectedTarget(null);
          setSearchQuery('');
          setDebouncedQuery('');
          setSearchResults([]);
          onSuccess();
        },
        onError: (err) => {
          setStatus({ kind: 'error', message: 'Merge failed: ' + err.message });
        },
      },
    );
  };

  const handleUnmerge = (secondaryId: string) => {
    unmergeMutation.mutate(
      { id: assetId, data: { secondary_asset_id: secondaryId } },
      {
        onSuccess: () => {
          setStatus({ kind: 'success', message: 'Merge undone successfully' });
          refetchHistory();
          onSuccess();
        },
        onError: (err) => {
          setStatus({ kind: 'error', message: 'Unmerge failed: ' + err.message });
        },
      },
    );
  };

  // Build preview fields from API response or fall back to local data
  const primaryPreview = (previewData?.primary as Record<string, unknown> | undefined) ?? assetData;
  const secondaryPreview = (previewData?.secondary as Record<string, unknown> | undefined) ?? (selectedTarget as unknown as Record<string, unknown> | null) ?? undefined;

  return (
    <div className={styles.tabContent}>
      <InlineStatus status={status} />
      {/* Merge history */}
      {mergeHistory.length > 0 && (
        <div className={styles.mergeHistory}>
          <div className={styles.mergeHistoryTitle}>Merge History</div>
          {mergeHistory.map((entry, idx) => {
            const isPrimary = entry.role === 'primary';
            const otherId = isPrimary ? entry.secondary_asset_id : entry.primary_asset_id;
            const otherHostname = isPrimary ? entry.secondary_hostname : entry.primary_hostname;
            const mergedAt = formatDate(entry.merged_at);
            const mergedBy = entry.merged_by ? String(entry.merged_by) : '';
            const label = isPrimary
              ? `Absorbed ${String(otherHostname || otherId)}`
              : `Merged into ${String(otherHostname || otherId)}`;

            return (
              <div key={idx} className={styles.mergeHistoryEntry}>
                <span>
                  <span className={styles.mergeHistoryLabel}>{label}</span>
                  <span className={styles.mergeHistoryMeta}>
                    {mergedAt}{mergedBy ? ` by ${mergedBy}` : ''}
                  </span>
                </span>
                {isPrimary && (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handleUnmerge(String(otherId))}
                    loading={unmergeMutation.isPending}
                  >
                    Undo
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Search */}
      <div className={styles.formRow}>
        <label className={styles.formLabel}>Search for asset to merge with</label>
        <input
          type="text"
          className={styles.mergeSearch}
          placeholder="Search by hostname, IP, or MAC..."
          value={searchQuery}
          onChange={handleSearchChange}
        />
      </div>

      {/* Results */}
      {(searchLoading || searchResults.length > 0 || (debouncedQuery.length >= 2 && !searchLoading)) && (
        <div className={styles.mergeResults}>
          {searchLoading && (
            <div className={styles.mergeEmptyText}>Searching...</div>
          )}
          {!searchLoading && searchResults.length === 0 && debouncedQuery.length >= 2 && (
            <div className={styles.mergeEmptyText}>No matching assets found</div>
          )}
          {!searchLoading && searchResults.map((result) => {
            const isSelected = selectedTarget?.id === result.id;
            const hostname = result.hostname ?? `Asset #${result.id}`;
            const details = [result.category, result.primary_ip, result.mac_address]
              .filter(Boolean)
              .join(' | ');
            return (
              <div
                key={result.id}
                className={[
                  styles.mergeResultRow,
                  isSelected ? styles.mergeResultSelected : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                onClick={() => handleSelectTarget(result)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') handleSelectTarget(result);
                }}
              >
                <div className={styles.mergeResultHostname}>{hostname}</div>
                {details && <div className={styles.mergeResultDetail}>{details}</div>}
              </div>
            );
          })}
        </div>
      )}

      {/* Preview */}
      {selectedTarget && (
        <>
          <div className={styles.mergePreview}>
            <div className={styles.mergeCard}>
              <div className={styles.mergeCardTitle}>Primary (kept)</div>
              {[
                ['Hostname', formatVal(primaryPreview?.hostname)],
                ['IP', formatVal(primaryPreview?.ip ?? primaryPreview?.primary_ip)],
                ['MAC', formatVal(primaryPreview?.mac ?? primaryPreview?.mac_address)],
                ['Category', formatVal(primaryPreview?.category)],
              ].map(([label, value]) => (
                <div key={label} className={styles.mergeField}>
                  <span className={styles.mergeFieldLabel}>{label}</span>
                  <span className={styles.mergeFieldValue}>{value}</span>
                </div>
              ))}
            </div>
            <div className={styles.mergeCard}>
              <div className={styles.mergeCardTitle}>Secondary (absorbed)</div>
              {[
                ['Hostname', formatVal(secondaryPreview?.hostname)],
                ['IP', formatVal(secondaryPreview?.ip ?? secondaryPreview?.primary_ip)],
                ['MAC', formatVal(secondaryPreview?.mac ?? secondaryPreview?.mac_address)],
                ['Category', formatVal(secondaryPreview?.category)],
              ].map(([label, value]) => (
                <div key={label} className={styles.mergeField}>
                  <span className={styles.mergeFieldLabel}>{label}</span>
                  <span className={styles.mergeFieldValue}>{value}</span>
                </div>
              ))}
            </div>
          </div>
          <div className={styles.mergeActions}>
            <Button
              variant="primary"
              size="sm"
              onClick={handleMerge}
              loading={mergeMutation.isPending}
            >
              Merge
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
