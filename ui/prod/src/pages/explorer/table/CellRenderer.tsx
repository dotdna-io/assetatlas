// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import { formatCellValue, lastSeenStatus, formatDate } from '../explorer-utils';
import { Badge } from '@/components/ui/Badge';
import { DataSourceBadge } from '@/components/ui/DataSourceBadge';
import { CategoryTag } from '@/components/ui/CategoryTag';
import styles from './table.module.css';

/**
 * Normalize a column field key to its bare semantic name so renderers can
 * recognize it regardless of FieldId encoding. Handles:
 *   "static:Category"      -> "category"
 *   "static:Data%20Source" -> "data source"
 *   "category"             -> "category"
 */
function fieldName(key: string): string {
  const colonIdx = key.indexOf(':');
  const tail = colonIdx >= 0 ? key.slice(colonIdx + 1) : key;
  try {
    return decodeURIComponent(tail).toLowerCase();
  } catch {
    return tail.toLowerCase();
  }
}

interface CellRendererProps {
  value: unknown;
  dataType: string;
  fieldKey: string;
  /** When set, each rendered value is right-clickable to add it to the search. */
  onContextMenu?: (e: React.MouseEvent, value: string) => void;
}

function MultiValueCell({
  values,
  renderItem,
  onItemContextMenu,
}: {
  values: string[];
  renderItem?: (v: string) => React.ReactNode;
  onItemContextMenu?: (e: React.MouseEvent, value: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (values.length === 0) return <span>-</span>;

  const render = renderItem || ((v: string) => v);

  // Wrap each value so right-click targets that specific item (not the whole
  // array). The overflow / expand badges stay outside the wrapper, so they
  // don't trigger the menu.
  const renderValue = (item: string) =>
    onItemContextMenu ? (
      <span onContextMenu={(e) => onItemContextMenu(e, item)}>{render(item)}</span>
    ) : (
      render(item)
    );

  if (values.length <= 2) {
    return (
      <div className={styles.arrayValues}>
        {values.map((item, i) => (
          <div key={i} className={styles.arrayValue}>{renderValue(item)}</div>
        ))}
      </div>
    );
  }
  const visible = expanded ? values : values.slice(0, 2);
  const overflow = values.length - 2;
  return (
    <div className={styles.arrayValues}>
      {visible.map((item, i) => (
        <div key={i} className={styles.arrayValue}>
          {renderValue(item)}
          {!expanded && i === visible.length - 1 && overflow > 0 && (
            <span
              className={styles.arrayOverflowBadge}
              onMouseDown={(e) => {
                e.stopPropagation();
                setExpanded(true);
              }}
            >
              +{overflow}
            </span>
          )}
          {expanded && i === visible.length - 1 && (
            <span
              className={styles.arrayOverflowBadge}
              onMouseDown={(e) => {
                e.stopPropagation();
                setExpanded(false);
              }}
            >
              -
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

export function CellRenderer({ value, dataType, fieldKey, onContextMenu }: CellRendererProps) {
  if (value === null || value === undefined) {
    return <span>-</span>;
  }

  const name = fieldName(fieldKey);
  const isDataSource = name === 'data source' || name === 'data_source';

  // Data source: multi-value, each rendered as a DataSourceBadge (icon + name).
  if (isDataSource) {
    const sources = Array.isArray(value) ? value.map(String) : [String(value)];
    return (
      <MultiValueCell
        values={sources}
        renderItem={(n) => <DataSourceBadge name={n} tinted />}
        onItemContextMenu={onContextMenu}
      />
    );
  }

  // String array: multi-value
  if (dataType === 'string_array' || (Array.isArray(value) && !isDataSource)) {
    const arr = Array.isArray(value) ? value.map(String) : [String(value)];
    return <MultiValueCell values={arr} onItemContextMenu={onContextMenu} />;
  }

  // Category tag — glossy icon chip + category-coloured name.
  if (name === 'category' && value) {
    return <CategoryTag category={String(value)} />;
  }

  // Confidence — plain colored text (no badge). High/Medium/Low by score or level.
  if ((name === 'confidence' || name === 'confidence_level' || name === 'confidence score') && value !== null) {
    const str = String(value).toLowerCase();
    let cls: string;
    let label: string;
    const num = parseFloat(str);
    if (!isNaN(num) && num <= 1) {
      const percent = Math.round(num * 100);
      cls = percent >= 80 ? styles.confHigh : percent >= 50 ? styles.confMedium : styles.confLow;
      label = percent >= 80 ? 'High' : percent >= 50 ? 'Medium' : 'Low';
    } else if (str === 'high') {
      cls = styles.confHigh; label = 'High';
    } else if (str === 'medium') {
      cls = styles.confMedium; label = 'Medium';
    } else {
      cls = styles.confLow; label = 'Low';
    }
    return <span className={cls}>{label}</span>;
  }

  // EOL status badge
  if (name === 'os_eol_status' && value) {
    const isEol = String(value).toLowerCase().includes('eol');
    return <Badge variant={isEol ? 'danger' : 'success'}>{isEol ? 'EOL' : 'Supported'}</Badge>;
  }

  // Last seen: color-coded by recency
  if (name === 'last seen' && value) {
    const status = lastSeenStatus(String(value));
    const statusClass =
      status === 'recent' ? styles.lastSeenRecent :
      status === 'aging' ? styles.lastSeenAging :
      styles.lastSeenStale;
    return (
      <span className={statusClass}>
        {formatDate(String(value))}
      </span>
    );
  }

  // Boolean
  if (dataType === 'boolean' || typeof value === 'boolean') {
    return <span>{value ? 'Yes' : 'No'}</span>;
  }

  // Timestamp / date
  if (dataType === 'timestamp' || dataType === 'date' || name === 'first seen') {
    return <span>{formatDate(String(value))}</span>;
  }

  // Default: use formatCellValue for everything else
  return <span>{formatCellValue(value, dataType, fieldKey)}</span>;
}
