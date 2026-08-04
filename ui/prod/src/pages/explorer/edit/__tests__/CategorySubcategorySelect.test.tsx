// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CategorySubcategorySelect } from '../CategorySubcategorySelect';

// Mutable add-subcategory mock so individual tests can drive the
// mutation's success / error callbacks.
const addMutate = vi.fn();
let addIsPending = false;

vi.mock('@/api/assets', () => ({
  useCategories: () => ({
    data: { categories: [{ name: 'Network', subcategories: ['Switch', 'Router'] }] },
    isLoading: false,
  }),
  useAddCustomSubcategory: () => ({ mutate: addMutate, isPending: addIsPending }),
}));

describe('CategorySubcategorySelect', () => {
  beforeEach(() => {
    addMutate.mockReset();
    addIsPending = false;
  });

  it('lists subcategories for the chosen category', () => {
    const onSub = vi.fn();
    render(
      <CategorySubcategorySelect
        category="Network"
        subcategory=""
        onCategoryChange={vi.fn()}
        onSubcategoryChange={onSub}
      />,
    );
    fireEvent.change(screen.getAllByRole('combobox')[1], { target: { value: 'Router' } });
    expect(onSub).toHaveBeenCalledWith('Router');
  });

  it('renders category options from data', () => {
    render(
      <CategorySubcategorySelect
        category=""
        subcategory=""
        onCategoryChange={vi.fn()}
        onSubcategoryChange={vi.fn()}
      />,
    );
    expect(screen.getByRole('option', { name: 'Network' })).toBeInTheDocument();
  });

  it('calls onCategoryChange and clears subcategory when category changes', () => {
    const onCat = vi.fn();
    const onSub = vi.fn();
    render(
      <CategorySubcategorySelect
        category="Network"
        subcategory="Switch"
        onCategoryChange={onCat}
        onSubcategoryChange={onSub}
      />,
    );
    fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'Network' } });
    expect(onCat).toHaveBeenCalledWith('Network');
    expect(onSub).toHaveBeenCalledWith('');
  });

  it('shows add-subcategory input when sentinel is selected', () => {
    render(
      <CategorySubcategorySelect
        category="Network"
        subcategory=""
        onCategoryChange={vi.fn()}
        onSubcategoryChange={vi.fn()}
      />,
    );
    // sentinel option should be present (category selected, subcategories exist)
    const select = screen.getAllByRole('combobox')[1];
    fireEvent.change(select, { target: { value: '__add_manual_subcategory__' } });
    expect(screen.getByPlaceholderText('New subcategory name...')).toBeInTheDocument();
  });

  // --- add-subcategory InlineStatus feedback (behavior parity with original) ---

  function enterAddMode() {
    fireEvent.change(screen.getAllByRole('combobox')[1], {
      target: { value: '__add_manual_subcategory__' },
    });
  }

  it('shows a success status when adding a subcategory succeeds', () => {
    // mutate resolves via its onSuccess callback with the new subcategory.
    addMutate.mockImplementation((_vars, opts) => opts.onSuccess({ subcategory: 'Firewall' }));
    const onSub = vi.fn();
    render(
      <CategorySubcategorySelect
        category="Network"
        subcategory=""
        onCategoryChange={vi.fn()}
        onSubcategoryChange={onSub}
      />,
    );
    enterAddMode();
    fireEvent.change(screen.getByPlaceholderText('New subcategory name...'), {
      target: { value: 'Firewall' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(screen.getByText('Subcategory "Firewall" added')).toBeInTheDocument();
    expect(onSub).toHaveBeenCalledWith('Firewall');
  });

  it('shows an error status when adding a subcategory fails', () => {
    addMutate.mockImplementation((_vars, opts) => opts.onError(new Error('boom')));
    render(
      <CategorySubcategorySelect
        category="Network"
        subcategory=""
        onCategoryChange={vi.fn()}
        onSubcategoryChange={vi.fn()}
      />,
    );
    enterAddMode();
    fireEvent.change(screen.getByPlaceholderText('New subcategory name...'), {
      target: { value: 'Firewall' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(screen.getByText('Failed to add subcategory: boom')).toBeInTheDocument();
  });

  it('shows a guard message and does not call mutate when the name is blank', () => {
    render(
      <CategorySubcategorySelect
        category="Network"
        subcategory=""
        onCategoryChange={vi.fn()}
        onSubcategoryChange={vi.fn()}
      />,
    );
    enterAddMode();
    // Whitespace-only name still trips the guard; the Add button is disabled on
    // empty input, so confirm via the Enter key on the input.
    const input = screen.getByPlaceholderText('New subcategory name...');
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(screen.getByText('Subcategory name is required')).toBeInTheDocument();
    expect(addMutate).not.toHaveBeenCalled();
  });
});
