// SPDX-License-Identifier: AGPL-3.0-or-later
import styles from './SearchInput.module.css';

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  width?: string | number;
  className?: string;
}

export function SearchInput({
  value,
  onChange,
  placeholder = 'Search...',
  width = 336,
  className,
}: SearchInputProps) {
  return (
    <input
      className={`${styles.input} ${className || ''}`}
      style={{ width }}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
