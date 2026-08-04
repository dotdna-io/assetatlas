// SPDX-License-Identifier: AGPL-3.0-or-later
import { useDocumentTitle } from '@/hooks';
import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useReports } from '@/api/reports';
import type { Report } from '@/api/reports';
import { Button } from '@/components/ui/Button';
import { SearchInput } from '@/components/ui/SearchInput';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { ReportCard } from './ReportCard';
import styles from './ReportingPage.module.css';

type SortOption = 'updated' | 'newest' | 'alpha';

function sortReports(reports: Report[], sortBy: SortOption): Report[] {
  const sorted = [...reports];
  switch (sortBy) {
    case 'updated':
      return sorted.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    case 'newest':
      return sorted.sort((a, b) => b.created_at.localeCompare(a.created_at));
    case 'alpha':
      return sorted.sort((a, b) => a.title.localeCompare(b.title));
    default:
      return sorted;
  }
}

export default function ReportingPage() {
  useDocumentTitle('Reports');
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState<SortOption>('updated');

  const navigate = useNavigate();
  const { data, isLoading, error } = useReports({ limit: 200 });

  const reports = useMemo(() => {
    if (!data?.reports) return [];
    let list = data.reports;
    if (search) {
      const q = search.toLowerCase();
      list = list.filter(
        (r) =>
          r.title.toLowerCase().includes(q) ||
          (r.description && r.description.toLowerCase().includes(q)),
      );
    }
    return sortReports(list, sortBy);
  }, [data?.reports, search, sortBy]);

  return (
    <div>
      <div className={styles.header}>
        <SearchInput
          className={styles.searchInput}
          value={search}
          onChange={setSearch}
          placeholder="Search reports..."
          width="100%"
        />
        <select
          className={styles.sortSelect}
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as SortOption)}
        >
          <option value="updated">Recently updated</option>
          <option value="newest">Newest first</option>
          <option value="alpha">Alphabetical</option>
        </select>
        <Button className={styles.createBtn} variant="primary" onClick={() => navigate('/reporting/new')}>
          Create Report
        </Button>
      </div>

      {isLoading && (
        <div className={styles.loadingWrap}>
          <LoadingSpinner />
        </div>
      )}

      {error && <div className={styles.errorMsg}>Failed to load reports</div>}

      {!isLoading && !error && reports.length === 0 && (
        <div className={styles.empty}>
          {search ? 'No reports match your search' : 'No reports yet'}
        </div>
      )}

      {reports.length > 0 && (
        <div className={styles.grid}>
          {reports.map((report) => (
            <ReportCard key={report.id} report={report} />
          ))}
        </div>
      )}
    </div>
  );
}
