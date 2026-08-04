// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/help/HelpPage.tsx
import { useMemo, useState } from 'react';
import { Link, useParams, useLocation } from 'react-router-dom';
import { TOPICS, getTopicById, getTopicContent } from './manifest';
import { CATEGORY_ORDER, CATEGORY_LABELS, type HelpTopic } from './types';
import { buildSearchIndex, searchHelp } from './helpSearch';
import { HelpMarkdown } from './HelpMarkdown';
import { BASE_PATH } from '@/basePath';
import styles from './HelpPage.module.css';

export function HelpPage() {
  const { topicId } = useParams();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? null;
  const [query, setQuery] = useState('');
  const index = useMemo(
    () => buildSearchIndex(TOPICS, getTopicContent),
    [],
  );
  const visible: HelpTopic[] = query.trim()
    ? searchHelp(index, query)
    : TOPICS;

  const byCategory = CATEGORY_ORDER.map((cat) => ({
    cat,
    items: visible
      .filter((t) => t.category === cat)
      .sort((a, b) => a.order - b.order),
  })).filter((g) => g.items.length > 0);

  const active = topicId ? getTopicById(topicId) : undefined;

  return (
    <div className={styles.page}>
      <aside className={styles.toc}>
        <input
          className={styles.search}
          placeholder="Search help…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {byCategory.map((g) => (
          <div key={g.cat} className={styles.tocGroup}>
            <div className={styles.tocCat}>{CATEGORY_LABELS[g.cat]}</div>
            {g.items.map((t) => (
              <Link
                key={t.id}
                to={`/help/${t.id}`}
                state={from ? { from } : undefined}
                className={[styles.tocItem, active?.id === t.id ? styles.tocItemActive : ''].filter(Boolean).join(' ')}
              >
                {t.title}
              </Link>
            ))}
          </div>
        ))}
      </aside>
      <main className={styles.content}>
        <div className={styles.topbar}>
          <Link to={from ?? '/dashboard'} className={styles.backLink}>
            ← Back
          </Link>
          <a
            className={styles.newTab}
            href={`${BASE_PATH}${location.pathname}`}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Open this help page in a new tab"
            title="Open in new tab"
          >
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M7 17 17 7" />
              <path d="M9 7h8v8" />
            </svg>
          </a>
        </div>
        {active ? (
          <HelpMarkdown content={getTopicContent(active)} />
        ) : (
          <p className={styles.placeholder}>
            Select a topic from the list to get started.
          </p>
        )}
      </main>
    </div>
  );
}
