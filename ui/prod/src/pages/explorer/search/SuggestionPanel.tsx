// SPDX-License-Identifier: AGPL-3.0-or-later
import { useRef, useEffect, useState } from 'react';
import { DataSourceBadge } from '@/components/ui/DataSourceBadge';
import styles from './search.module.css';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SuggestionMode = 'field' | 'operator' | 'value' | null;

export interface SuggestionItem {
  text: string;       // display text
  value: string;      // value to insert
  source?: string;    // data source name (for field suggestion grouping)
  score?: number;     // relevance score
  count?: number;     // value count (for value suggestions)
}

export interface SuggestionPanelProps {
  mode: SuggestionMode;
  items: SuggestionItem[];
  selectedIndex: number;
  onSelect: (item: SuggestionItem) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Group items by their `source` field, preserving order of first appearance. */
export function groupBySource(items: SuggestionItem[]): { source: string; items: SuggestionItem[] }[] {
  const groups: Map<string, SuggestionItem[]> = new Map();
  for (const item of items) {
    const key = item.source ?? 'AssetAtlas';
    if (!groups.has(key)) {
      groups.set(key, []);
    }
    groups.get(key)!.push(item);
  }

  // Order: AssetAtlas first, then integration sources by match count desc, Tags last
  const ordered: { source: string; items: SuggestionItem[] }[] = [];
  if (groups.has('AssetAtlas')) {
    ordered.push({ source: 'AssetAtlas', items: groups.get('AssetAtlas')! });
    groups.delete('AssetAtlas');
  }

  let tagsGroup: SuggestionItem[] | undefined;
  if (groups.has('Tags')) {
    tagsGroup = groups.get('Tags');
    groups.delete('Tags');
  }

  // Remaining sources sorted by match count desc, then alphabetically
  const remaining = Array.from(groups.entries()).sort((a, b) => {
    const countDiff = b[1].length - a[1].length;
    if (countDiff !== 0) return countDiff;
    return a[0].localeCompare(b[0]);
  });
  for (const [source, groupItems] of remaining) {
    ordered.push({ source, items: groupItems });
  }

  if (tagsGroup) {
    ordered.push({ source: 'Tags', items: tagsGroup });
  }

  return ordered;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SuggestionPanel({
  mode,
  items,
  selectedIndex,
  onSelect,
}: SuggestionPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [valueExpanded, setValueExpanded] = useState(false);

  // Scroll the selected item into view when keyboard-navigating
  useEffect(() => {
    if (selectedIndex < 0 || !panelRef.current) return;
    const el = panelRef.current.querySelector(`[data-suggestion-index="${selectedIndex}"]`);
    if (el) {
      el.scrollIntoView({ block: 'nearest' });
    }
  }, [selectedIndex]);

  // Reset value expansion when items change
  useEffect(() => {
    setValueExpanded(false);
  }, [items]);

  if (!mode || items.length === 0) return null;

  // --- Field mode: horizontal columns grouped by source ---
  if (mode === 'field') {
    const groups = groupBySource(items);
    let flatIndex = 0;

    return (
      <div ref={panelRef} className={styles.panel}>
        <div className={styles.fieldColumns}>
          {groups.map((group) => {
            const startIndex = flatIndex;
            return (
              <div key={group.source} className={styles.fieldColumn}>
                <div className={styles.fieldGroupHeader}>
                  <DataSourceBadge name={group.source} tinted className={styles.sourceBadge} />
                </div>
                {group.items.map((item) => {
                  const idx = flatIndex++;
                  return (
                    <div
                      key={`${group.source}-${item.value}-${startIndex}`}
                      className={`${styles.fieldItem} ${idx === selectedIndex ? styles.selected : ''}`}
                      data-suggestion-index={idx}
                      onMouseDown={(e) => {
                        e.preventDefault(); // keep focus on input
                        onSelect(item);
                      }}
                    >
                      {item.text}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
        <div className={styles.panelHint}>
          <kbd className={styles.kbd}>Tab</kbd>
          <kbd className={styles.kbd}>Arrows</kbd>
          <kbd className={styles.kbd}>Enter</kbd>
        </div>
      </div>
    );
  }

  // --- Operator mode: inline items with separators ---
  if (mode === 'operator') {
    return (
      <div ref={panelRef} className={styles.panel}>
        <div className={styles.operatorRow}>
          {items.map((item, idx) => (
            <span key={item.value}>
              {idx > 0 && <span className={styles.operatorSep}>|</span>}
              <span
                className={`${styles.operatorItem} ${idx === selectedIndex ? styles.selected : ''}`}
                data-suggestion-index={idx}
                onMouseDown={(e) => {
                  e.preventDefault();
                  onSelect(item);
                }}
              >
                {item.text}
              </span>
            </span>
          ))}
        </div>
        <div className={styles.panelHint}>
          <kbd className={styles.kbd}>Tab</kbd>
          <kbd className={styles.kbd}>Arrows</kbd>
          <kbd className={styles.kbd}>Enter</kbd>
        </div>
      </div>
    );
  }

  // --- Value mode: plain text in a single row with "others+" overflow ---
  const VISIBLE_VALUES = 8;
  const visibleItems = valueExpanded ? items : items.slice(0, VISIBLE_VALUES);
  const overflowCount = items.length - VISIBLE_VALUES;

  return (
    <div ref={panelRef} className={styles.panel}>
      <div className={styles.valueRow}>
        {visibleItems.map((item, idx) => (
          <span key={item.value}>
            {idx > 0 && <span className={styles.valueSep}>|</span>}
            <span
              className={`${styles.valueItem} ${idx === selectedIndex ? styles.selected : ''}`}
              data-suggestion-index={idx}
              onMouseDown={(e) => {
                e.preventDefault();
                onSelect(item);
              }}
            >
              {item.text}
            </span>
          </span>
        ))}
        {!valueExpanded && overflowCount > 0 && (
          <span
            className={styles.valueOverflow}
            onMouseDown={(e) => {
              e.preventDefault();
              setValueExpanded(true);
            }}
          >
            +{overflowCount} others
          </span>
        )}
      </div>
      <div className={styles.panelHint}>
        <kbd className={styles.kbd}>Tab</kbd>
        <kbd className={styles.kbd}>Left/Right</kbd>
        <kbd className={styles.kbd}>Enter</kbd>
      </div>
    </div>
  );
}
