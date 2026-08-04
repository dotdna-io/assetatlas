// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ThresholdEditor } from '../wizard/ThresholdEditor';

describe('ThresholdEditor', () => {
  it('emits a default threshold when a condition is picked', () => {
    const onChange = vi.fn();
    render(<ThresholdEditor value={null} onChange={onChange} />);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'exceeds' } });
    expect(onChange).toHaveBeenCalledWith({ condition: 'exceeds', value: 80, unit: 'percent' });
  });

  it('emits null when condition is cleared', () => {
    const onChange = vi.fn();
    render(
      <ThresholdEditor
        value={{ condition: 'falls_below', value: 50, unit: 'number' }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('updates value and unit of an existing threshold', () => {
    const onChange = vi.fn();
    render(
      <ThresholdEditor
        value={{ condition: 'exceeds', value: 80, unit: 'percent' }}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '95' } });
    expect(onChange).toHaveBeenCalledWith({ condition: 'exceeds', value: 95, unit: 'percent' });
    fireEvent.click(screen.getByRole('button', { name: '#' }));
    expect(onChange).toHaveBeenCalledWith({ condition: 'exceeds', value: 80, unit: 'number' });
  });
});
