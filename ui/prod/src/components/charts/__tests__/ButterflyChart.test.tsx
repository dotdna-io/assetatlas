// SPDX-License-Identifier: AGPL-3.0-or-later
import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ButterflyChart } from '../ButterflyChart';

describe('ButterflyChart', () => {
  it('renders no yellow returned bar when returnedLabel is absent', () => {
    const { container } = render(
      <ButterflyChart data={[{ label: 'Jun 10', added: 5, removed: 2 }]} />,
    );
    expect(container.querySelector('[data-testid="bar-returned"]')).toBeNull();
  });

  it('renders a stacked yellow bar when returned is provided', () => {
    const { container } = render(
      <ButterflyChart
        data={[{ label: 'Jun 10', added: 5, returned: 8, removed: 2 }]}
        returnedLabel="Returned"
        topLabel="New"
        bottomLabel="Departed"
      />,
    );
    expect(container.querySelector('[data-testid="bar-returned"]')).not.toBeNull();
  });

  it('portals the hover tooltip outside the chart subtree', () => {
    // The chart lives inside glossy cards whose backdrop-filter makes the
    // card the containing block for position:fixed descendants — an inline
    // tooltip is mispositioned and clipped by the card's overflow:hidden.
    // It must therefore mount under document.body, not inside the wrap.
    const { container } = render(
      <ButterflyChart data={[{ label: 'Jun 10', added: 5, removed: 2 }]} />,
    );
    const bar = container.querySelector('[data-testid="bar-added"]');
    expect(bar).not.toBeNull();
    fireEvent.mouseMove(bar!);
    const tip = document.body.querySelector('[data-testid="butterfly-tooltip"]');
    expect(tip).not.toBeNull();
    expect(tip!.textContent).toContain('Jun 10');
    expect(container.contains(tip)).toBe(false);
  });
});
