// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useRef, useCallback, useId } from 'react';
import { useAssetSearch } from '@/api/assets';
import styles from './AlertsOmniSearch.module.css';

interface AssetOption {
  id: string;
  hostname: string | null;
  primary_ip: string | null;
}

interface AlertsOmniSearchProps {
  /** The committed title/description text search (mirrored into the input). */
  search: string;
  /** The active asset filter id, or '' when none is set. */
  assetId: string;
  /** Human label for the asset token (hostname); falls back to the id. */
  assetLabel: string;
  /** Commit the typed text as the server-side `search` (Enter / search row). */
  onSearch: (text: string) => void;
  /** Pick an asset → set the token + the server-side `asset_id`. */
  onAssetSelect: (id: string, label: string) => void;
  /** Clear the asset token (the ✕). */
  onAssetClear: () => void;
  width?: number;
}

/**
 * Alerts-page omni-search: one box that does two things at once.
 *
 * Typing runs a debounced /assets/search and opens a dropdown of assets plus a
 * "Search alerts for '<text>'" action row. Picking an asset sets an exact,
 * server-side `asset_id` filter (rendered as a token in the box); Enter or the
 * action row commits the typed text as the title/description `search`. The two
 * coexist and AND-combine on the server — text search is intentionally
 * Enter-triggered here, not live-on-type.
 *
 * It replaces the plain SearchInput on the Alerts page ONLY — the shared
 * SearchInput is untouched. Model: MergeTab's typeahead (debounce + request).
 */
export function AlertsOmniSearch({
  search,
  assetId,
  assetLabel,
  onSearch,
  onAssetSelect,
  onAssetClear,
  width = 420,
}: AlertsOmniSearchProps) {
  const [query, setQuery] = useState(search);
  const [debounced, setDebounced] = useState('');
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const listboxId = useId();

  // Debounce the typed text before asking the server for asset suggestions,
  // matching MergeTab's ~250-300ms typeahead cadence.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 250);
    return () => clearTimeout(t);
  }, [query]);

  // Only fetch while the dropdown is open, so committing/clearing does not fire
  // a stray suggestion request. Empty q disables the query (see useAssetSearch).
  const { data } = useAssetSearch({ q: open ? debounced : '', limit: 8 });
  // Only surface suggestions for the term currently in the box. While the
  // debounce still trails a fast edit, `data` is react-query's cache for the
  // PREVIOUS term, which would otherwise flash stale assets under the new query.
  const results: AssetOption[] = debounced === query.trim() ? (data?.results ?? []) : [];

  // Reset the keyboard highlight whenever the option set changes.
  useEffect(() => {
    setHighlight(-1);
  }, [debounced, open]);

  // Close on an outside click (the standard typeahead dismissal).
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const showDropdown = open && query.trim().length > 0;
  const optionCount = results.length + 1; // asset rows + the "search alerts" row

  const pickAsset = useCallback(
    (r: AssetOption) => {
      onAssetSelect(r.id, r.hostname || r.primary_ip || r.id);
      // Discard the typed asset query; keep any committed text search visible.
      setQuery(search);
      setOpen(false);
    },
    [onAssetSelect, search],
  );

  const applySearch = useCallback(() => {
    onSearch(query.trim());
    setOpen(false);
  }, [onSearch, query]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, optionCount - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, -1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (highlight >= 0 && highlight < results.length) pickAsset(results[highlight]);
      else applySearch();
    } else if (e.key === 'Escape') {
      setOpen(false);
      setHighlight(-1);
    }
  };

  return (
    <div className={styles.root} ref={containerRef} style={{ width }}>
      <div className={styles.box}>
        {assetId && (
          <span className={styles.token}>
            <span className={styles.tokenLabel} title={assetLabel || assetId}>
              {assetLabel || assetId}
            </span>
            <button
              type="button"
              className={styles.tokenClose}
              onClick={onAssetClear}
              aria-label="Clear asset filter"
            >
              ×
            </button>
          </span>
        )}
        <input
          className={styles.input}
          value={query}
          placeholder="Search alerts or an asset…"
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => {
            if (query.trim()) setOpen(true);
          }}
          onKeyDown={onKeyDown}
          role="combobox"
          aria-expanded={showDropdown}
          aria-controls={showDropdown ? listboxId : undefined}
          aria-autocomplete="list"
        />
      </div>

      {showDropdown && (
        <div className={styles.dropdown} id={listboxId} role="listbox">
          {results.length > 0 && (
            <div className={styles.section}>
              <div className={styles.sectionLabel}>Assets</div>
              {results.map((r, i) => (
                <button
                  key={r.id}
                  type="button"
                  role="option"
                  aria-selected={highlight === i}
                  className={`${styles.option} ${highlight === i ? styles.optionActive : ''}`}
                  onMouseEnter={() => setHighlight(i)}
                  onClick={() => pickAsset(r)}
                >
                  <span className={styles.optionHost}>{r.hostname || '(no hostname)'}</span>
                  {r.primary_ip && <span className={styles.optionMeta}>{r.primary_ip}</span>}
                </button>
              ))}
            </div>
          )}
          <button
            type="button"
            role="option"
            aria-selected={highlight === results.length}
            className={`${styles.searchRow} ${highlight === results.length ? styles.optionActive : ''}`}
            onMouseEnter={() => setHighlight(results.length)}
            onClick={applySearch}
          >
            Search alerts for “<strong>{query.trim()}</strong>”
          </button>
        </div>
      )}
    </div>
  );
}
