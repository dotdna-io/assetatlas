// SPDX-License-Identifier: AGPL-3.0-or-later
import { CategoryIcon, categoryKey, getCategoryColor } from '@/components/ui/CategoryIcon';
import styles from './CategoryTag.module.css';

// Display labels for the known categories — gives correct casing regardless
// of how the source value is cased ("server"/"Server" → "Server", "iot" → "IoT").
const LABELS: Record<string, string> = {
  server: 'Server',
  workstation: 'Workstation',
  mobile: 'Mobile',
  network: 'Network',
  iot: 'IoT',
  ot: 'OT',
  miscellaneous: 'Miscellaneous',
};

interface CategoryTagProps {
  category: string;
  className?: string;
  // Render just the colored icon (no name). The label is kept as a tooltip /
  // aria-label so the category is still identifiable.
  iconOnly?: boolean;
}

/**
 * Asset-category tag: a plain category-coloured icon followed by the category
 * name in the same colour. Replaces the old glossy <Badge> for categories.
 * (Data sources keep their glossy chip; categories use the bare icon.)
 * Pass `iconOnly` for a compact icon-only chip.
 */
export function CategoryTag({ category, className, iconOnly }: CategoryTagProps) {
  const key = categoryKey(category);
  const color = getCategoryColor(key);
  const label = LABELS[key] ?? category;

  if (iconOnly) {
    return (
      <span
        className={`${styles.iconOnly} ${className ?? ''}`}
        style={{ color }}
        title={label}
        aria-label={label}
      >
        <CategoryIcon category={key} size={16} />
      </span>
    );
  }

  return (
    <span className={`${styles.tag} ${className ?? ''}`}>
      <span className={styles.icon} style={{ color }}>
        <CategoryIcon category={key} size={16} />
      </span>
      <span className={styles.name} style={{ color }}>{label}</span>
    </span>
  );
}
