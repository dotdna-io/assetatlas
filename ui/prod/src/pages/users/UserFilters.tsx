// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useRef } from 'react';
import styles from './users.module.css';

interface UserFiltersProps {
  search: string;
  role: string;
  status: string;
  onSearchChange: (value: string) => void;
  onRoleChange: (value: string) => void;
  onStatusChange: (value: string) => void;
}

export function UserFilters({
  search,
  role,
  status,
  onSearchChange,
  onRoleChange,
  onStatusChange,
}: UserFiltersProps) {
  const [localSearch, setLocalSearch] = useState(search);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    setLocalSearch(search);
  }, [search]);

  function handleSearchInput(value: string) {
    setLocalSearch(value);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => onSearchChange(value), 300);
  }

  useEffect(() => () => clearTimeout(debounceRef.current), []);

  return (
    <div className={styles.filters}>
      <div className={styles.searchWrapper}>
        <svg className={styles.searchIcon} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          type="text"
          className={styles.searchInput}
          placeholder="Search by username or email..."
          value={localSearch}
          onChange={(e) => handleSearchInput(e.target.value)}
        />
      </div>
      <select
        className={styles.filterSelect}
        value={role}
        onChange={(e) => onRoleChange(e.target.value)}
      >
        <option value="">All Roles</option>
        <option value="user">User</option>
        <option value="admin">Admin</option>
        <option value="super_admin">Super Admin</option>
      </select>
      <select
        className={styles.filterSelect}
        value={status}
        onChange={(e) => onStatusChange(e.target.value)}
      >
        <option value="">All Status</option>
        <option value="true">Active</option>
        <option value="false">Inactive</option>
      </select>
    </div>
  );
}
