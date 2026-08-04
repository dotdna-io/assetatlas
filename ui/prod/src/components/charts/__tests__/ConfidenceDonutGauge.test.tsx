// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { fireEvent, render } from '@testing-library/react';

import { ConfidenceDonutGauge } from '../ConfidenceDonutGauge';

const HIGH = '#10b981';
const MED = '#f59e0b';
const LOW = '#ef4444';

describe('ConfidenceDonutGauge', () => {
  it('draws the ring as a full circle when every asset falls in one band', () => {
    // One band = a single 360° slice. As one SVG arc its start/end points are
    // pixel-coincident, so the browser omits it and the ring vanishes, leaving
    // only the centre text (the reported bug). It must be drawn as a circle.
    const { container } = render(
      <ConfidenceDonutGauge high={12} medium={0} low={0} avg={80} animate={false} />,
    );
    expect(container.querySelector(`circle[stroke="${HIGH}"]`)).not.toBeNull();
  });

  it('still renders all three arcs when assets span every band', () => {
    const { container } = render(
      <ConfidenceDonutGauge high={5} medium={3} low={2} avg={50} animate={false} />,
    );
    expect(container.querySelector(`path[stroke="${HIGH}"]`)).not.toBeNull();
    expect(container.querySelector(`path[stroke="${MED}"]`)).not.toBeNull();
    expect(container.querySelector(`path[stroke="${LOW}"]`)).not.toBeNull();
  });

  it('portals the hover tooltip outside the chart subtree', () => {
    // Same failure mode as ButterflyChart: the gauge sits inside glossy cards
    // whose backdrop-filter makes the card both containing block and stacking
    // context for the fixed tooltip — rendered inline it is mispositioned and
    // paints underneath the neighbouring card. It must mount on document.body.
    const { container } = render(
      <ConfidenceDonutGauge high={5} medium={3} low={2} avg={50} animate={false} />,
    );
    const slice = container.querySelector(`path[stroke="${HIGH}"]`);
    expect(slice).not.toBeNull();
    fireEvent.mouseMove(slice!);
    const tip = document.body.querySelector('[data-testid="gauge-tooltip"]');
    expect(tip).not.toBeNull();
    expect(container.contains(tip)).toBe(false);
  });
});
