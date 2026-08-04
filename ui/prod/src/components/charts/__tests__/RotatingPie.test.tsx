// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { fireEvent, render } from '@testing-library/react';

import { RotatingPie } from '../RotatingPie';

const RED = '#ef4444';
const BLUE = '#3b82f6';

describe('RotatingPie', () => {
  it('renders one wedge per non-zero slice and skips zero slices', () => {
    const { container } = render(
      <RotatingPie
        animate={false}
        slices={[
          { label: 'High', value: 7, color: RED },
          { label: 'Low', value: 1, color: BLUE },
          { label: 'Medium', value: 0, color: '#f59e0b' },
        ]}
      />,
    );
    expect(container.querySelector(`path[fill="${RED}"]`)).not.toBeNull();
    expect(container.querySelector(`path[fill="${BLUE}"]`)).not.toBeNull();
    expect(container.querySelector('path[fill="#f59e0b"]')).toBeNull();
  });

  it('draws a full disc when a single slice holds everything', () => {
    // A 360° wedge's arc endpoints coincide, so the browser omits the path —
    // it must be drawn as a circle (same fix as ConfidenceDonutGauge).
    const { container } = render(
      <RotatingPie animate={false} slices={[{ label: 'High', value: 5, color: RED }]} />,
    );
    expect(container.querySelector(`circle[fill="${RED}"]`)).not.toBeNull();
  });

  it('renders a placeholder disc when every slice is zero', () => {
    const { container } = render(
      <RotatingPie animate={false} slices={[{ label: 'High', value: 0, color: RED }]} />,
    );
    expect(container.querySelector('circle')).not.toBeNull();
    expect(container.querySelector(`circle[fill="${RED}"]`)).toBeNull();
  });

  it('portals the hover tooltip outside the chart subtree', () => {
    // Same failure mode as ButterflyChart/ConfidenceDonutGauge: glossy cards'
    // backdrop-filter makes the card the containing block and stacking context
    // for the fixed tooltip — rendered inline it paints under the neighbouring
    // card. It must mount on document.body.
    const { container } = render(
      <RotatingPie
        animate={false}
        slices={[
          { label: 'High', value: 7, color: RED },
          { label: 'Low', value: 1, color: BLUE },
        ]}
      />,
    );
    const wedge = container.querySelector(`path[fill="${RED}"]`);
    expect(wedge).not.toBeNull();
    fireEvent.mouseMove(wedge!);
    const tip = document.body.querySelector('[data-testid="pie-tooltip"]');
    expect(tip).not.toBeNull();
    expect(container.contains(tip)).toBe(false);
  });
});
