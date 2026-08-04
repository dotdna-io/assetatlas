// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useRef } from 'react';
import { useCategories, useAddCustomSubcategory } from '@/api/assets';
import { Button } from '@/components/ui/Button';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import styles from './edit.module.css';

export const ADD_SUBCATEGORY_SENTINEL = '__add_manual_subcategory__';

interface Props {
  category: string;
  subcategory: string;
  onCategoryChange: (category: string) => void;
  onSubcategoryChange: (subcategory: string) => void;
  disabled?: boolean;
}

export function CategorySubcategorySelect({
  category,
  subcategory,
  onCategoryChange,
  onSubcategoryChange,
  disabled,
}: Props) {
  const [status, setStatus] = useInlineStatus();
  const { data: categoriesData, isLoading: categoriesLoading } = useCategories();
  const addSubcategoryMutation = useAddCustomSubcategory();
  const [isAddingSubcategory, setIsAddingSubcategory] = useState(false);
  const [newSubcategoryName, setNewSubcategoryName] = useState('');
  // Remembers the subcategory that was selected before entering add mode so we
  // can restore it if the user clicks Cancel.
  const prevSubcategoryRef = useRef('');

  const categories = categoriesData?.categories ?? [];
  // Drop any catalog entry colliding with the sentinel literal so the
  // dropdown stays unambiguous.
  const subcategories = category
    ? (categories.find((c) => c.name === category)?.subcategories ?? []).filter(
        (s) => s !== ADD_SUBCATEGORY_SENTINEL,
      )
    : [];

  const handleCategoryChange = (cat: string) => {
    onCategoryChange(cat);
    onSubcategoryChange('');
    setIsAddingSubcategory(false);
    setNewSubcategoryName('');
  };

  const handleSubcategoryChange = (value: string) => {
    if (value === ADD_SUBCATEGORY_SENTINEL) {
      // Remember current selection so cancel can restore it.  Clear it in the
      // parent so the Save button becomes disabled while the user is typing.
      prevSubcategoryRef.current = subcategory;
      setIsAddingSubcategory(true);
      setNewSubcategoryName('');
      onSubcategoryChange('');
      return;
    }
    onSubcategoryChange(value);
  };

  const handleCancelAdd = () => {
    setIsAddingSubcategory(false);
    setNewSubcategoryName('');
    onSubcategoryChange(prevSubcategoryRef.current);
  };

  const handleConfirmAdd = () => {
    const trimmed = newSubcategoryName.trim();
    if (!trimmed) {
      setStatus({ kind: 'error', message: 'Subcategory name is required' });
      return;
    }

    addSubcategoryMutation.mutate(
      { category, name: trimmed },
      {
        onSuccess: (result) => {
          setStatus({
            kind: 'success',
            message: `Subcategory "${result.subcategory}" added`,
          });
          setIsAddingSubcategory(false);
          setNewSubcategoryName('');
          onSubcategoryChange(result.subcategory);
        },
        onError: (err) => {
          setStatus({ kind: 'error', message: 'Failed to add subcategory: ' + err.message });
        },
      },
    );
  };

  const handleAddKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleConfirmAdd();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      handleCancelAdd();
    }
  };

  const canAddSubcategory = !!category && subcategories.length > 0;

  return (
    <>
      <InlineStatus status={status} />
      <div className={styles.formRow}>
        <label className={styles.formLabel}>Category</label>
        <select
          className={styles.selectInput}
          value={category}
          onChange={(e) => handleCategoryChange(e.target.value)}
          disabled={disabled || categoriesLoading}
        >
          <option value="">{categoriesLoading ? 'Loading...' : 'Select category...'}</option>
          {categories.map((cat) => (
            <option key={cat.name} value={cat.name}>
              {cat.name}
            </option>
          ))}
        </select>
      </div>

      <div className={styles.formRow}>
        <label className={styles.formLabel}>Subcategory</label>
        {isAddingSubcategory ? (
          <div className={styles.addTagRow}>
            <input
              type="text"
              className={styles.textInput}
              placeholder="New subcategory name..."
              value={newSubcategoryName}
              onChange={(e) => setNewSubcategoryName(e.target.value)}
              onKeyDown={handleAddKeyDown}
              maxLength={64}
              autoFocus
            />
            <Button
              variant="primary"
              size="sm"
              onClick={handleConfirmAdd}
              loading={addSubcategoryMutation.isPending}
              disabled={!newSubcategoryName.trim()}
            >
              Add
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={handleCancelAdd}
              disabled={addSubcategoryMutation.isPending}
            >
              Cancel
            </Button>
          </div>
        ) : (
          <select
            className={styles.selectInput}
            value={subcategory}
            onChange={(e) => handleSubcategoryChange(e.target.value)}
            disabled={disabled || !category || subcategories.length === 0}
          >
            <option value="">Select subcategory...</option>
            {subcategories.map((sub) => (
              <option key={sub} value={sub}>
                {sub}
              </option>
            ))}
            {canAddSubcategory && (
              <option value={ADD_SUBCATEGORY_SENTINEL}>+ Add manual subcategory…</option>
            )}
          </select>
        )}
      </div>
    </>
  );
}
