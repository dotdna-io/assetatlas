// SPDX-License-Identifier: AGPL-3.0-or-later
import { memo, useState } from 'react';

import { assetsApi } from '@/api/assets';
import { getApiErrorMessage } from '@/api/client';
import { downloadBlob } from '@/utils/clipboard';
import { CategoryTag } from '@/components/ui/CategoryTag';
import { isKnownCategory } from '@/components/ui/CategoryIcon';
import { formatDate } from '@/pages/explorer/explorer-utils';

import type { TableArtifact } from './chartArtifactTypes';
import styles from './TableRenderer.module.css';

interface Props {
  artifact: TableArtifact;
}

const DATE_KEYS = new Set(['last_seen', 'first_seen']);
const CATEGORY_KEYS = new Set(['category', 'subcategory']);

// Tables whose meta.query is a re-runnable LIST query the /assets/export.csv
// route accepts. search_assets is intentionally excluded — its result is a
// fuzzy-match shape, not the list query the export route re-runs.
const LIST_EXPORT_TOOLS = new Set(['list_assets', 'query_assets', 'export_assets']);


/** Render a single cell value by column key. Mirrors the explorer's visible
 *  rendering for the columns we emit:
 *    - category / subcategory  → colored Badge (shared palette)
 *    - last_seen / first_seen  → formatDate
 *    - arrays                  → comma-joined, capped at 3 with "+N" suffix
 *    - everything else         → plain string */
function renderCell(key: string, value: unknown): React.ReactNode {
  if (value === null || value === undefined || value === '') {
    return <span>-</span>;
  }
  if (CATEGORY_KEYS.has(key) && typeof value === 'string') {
    if (isKnownCategory(value)) {
      return <CategoryTag category={value} />;
    }
    return <span>{value}</span>;
  }
  if (DATE_KEYS.has(key) && typeof value === 'string') {
    return <span>{formatDate(value)}</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span>-</span>;
    const head = value.slice(0, 3).map(String).join(', ');
    const overflow = value.length - 3;
    return overflow > 0
      ? <span>{head} <small>+{overflow}</small></span>
      : <span>{head}</span>;
  }
  return <span>{String(value)}</span>;
}


/** Render the value cell for a keyvalue-layout row.
 *  Checks the row's `field` label to trigger category coloring — the column
 *  key in this layout is always 'value', not 'category', so renderCell alone
 *  would miss the Badge. */
function renderKeyValueCell(row: Record<string, unknown>): React.ReactNode {
  const fieldLabel = String(row.field ?? '').toLowerCase();
  const value = row.value;
  if ((fieldLabel === 'category' || fieldLabel === 'subcategory')
      && typeof value === 'string' && isKnownCategory(value)) {
    return <CategoryTag category={value} />;
  }
  return renderCell('value', value);
}


function TableRendererInner({ artifact }: Props) {
  const { title, columns, series, meta } = artifact;
  // List-shaped tables export via /assets/export.csv (columns == meta.query.
  // fields); the aggregate matrix exports via /assets/aggregate.csv.
  const canExportList =
    meta.layout === 'list' && LIST_EXPORT_TOOLS.has(meta.source_tool) && !!meta.query;
  const canExportAggregate = meta.layout === 'aggregate' && !!meta.query;
  const canExport = canExportList || canExportAggregate;
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDownload = async () => {
    if (!meta.query) return;
    setDownloading(true);
    setError(null);
    try {
      const { blob, filename } =
        meta.layout === 'aggregate'
          ? await assetsApi.exportAggregateCsv(meta.query)
          : await assetsApi.exportAssetsCsv(meta.query);
      downloadBlob(blob, filename);
    } catch (e) {
      setError(getApiErrorMessage(e, 'Download failed'));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className={styles.artifact}>
      <div className={styles.header}>
        <div className={styles.title}>{title}</div>
        {canExport && (
          <button
            type="button"
            className={styles.downloadBtn}
            onClick={handleDownload}
            disabled={downloading}
            title="Download the full result set as CSV"
          >
            {downloading ? 'Preparing…' : 'Download CSV'}
          </button>
        )}
      </div>
      {error && <div className={styles.error}>{error}</div>}
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c.key} scope="col">{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {series.map((row, i) => (
              <tr key={i}>
                {meta.layout === 'keyvalue'
                  ? <>
                      <td>{String(row.field ?? '')}</td>
                      <td>{renderKeyValueCell(row)}</td>
                    </>
                  : columns.map((c) => (
                      <td key={c.key}>{renderCell(c.key, row[c.key])}</td>
                    ))
                }
              </tr>
            ))}
          </tbody>
        </table>
        {meta.truncated && meta.total !== undefined && (
          <div className={styles.footnote}>
            Showing first {series.length} of {meta.total}.
          </div>
        )}
      </div>
    </div>
  );
}

export const TableRenderer = memo(TableRendererInner);
