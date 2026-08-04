// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, beforeEach } from 'vitest';
// Side-effect import: the vanilla card registers itself on window (jsdom). This
// reliably yields the class regardless of CJS/ESM interop.
import '../../../../../public/dotdna_charts_js/charts/NumberCard.js';

const DotDNANumberCard = (window as any).DotDNANumberCard as new (id: string, opts: any) => any;

function mount(options: Record<string, unknown>) {
  document.body.innerHTML = '<div id="card"></div>';
  return new DotDNANumberCard('card', { animate: false, ...options });
}
const valueText = () => document.querySelector('#card .value-number')?.textContent;
const labelEl = () => document.querySelector('#card .dotdna-number-card-label');
const suffixEl = () => document.querySelector('#card .dotdna-number-card-suffix');

describe('DotDNANumberCard', () => {
  beforeEach(() => { document.body.innerHTML = ''; });

  it('renders the value from DotDNAChart-shaped { data: { value } } (not a random default)', () => {
    mount({ data: { value: 42 } });
    expect(valueText()).toBe('42');
  });

  it('never shows the "Total Revenue" placeholder and omits the label when none is given', () => {
    mount({ data: { value: 1 } });
    expect(labelEl()).toBeNull();
    expect(document.body.textContent).not.toContain('Total Revenue');
  });

  it('shows a pre-formatted string value verbatim with no NaN and no magnitude suffix', () => {
    mount({ data: { value: '2.34 GB' } });
    expect(valueText()).toBe('2.34 GB');
    expect(suffixEl()).toBeNull();
  });

  it('does not append a bogus K/M suffix to large numbers', () => {
    mount({ data: { value: 6939 } });
    expect(valueText()).toBe('6,939');
    expect(suffixEl()).toBeNull();
  });

  it('updates on setData (the refetch path that the old card ignored)', () => {
    const card = mount({ data: { value: 1 } });
    card.setData({ value: 7 });
    expect(valueText()).toBe('7');
  });

  it('shows an em dash for no data (empty-array mount)', () => {
    mount({ data: [] });
    expect(valueText()).toBe('—');
  });
});
