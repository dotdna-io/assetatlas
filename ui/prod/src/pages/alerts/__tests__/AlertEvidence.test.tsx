// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { AlertEvidence } from '../AlertEvidence';

const narrative = '1,403 new assets appeared today — far more than the usual 210.';

// New shape (Task 5a): stats moved out of evidence.baseline/evidence.score into
// evidence.technical; evidence.typical is the plain top-level "usual" value.
const evidence = {
  detector: 'mass_new_assets',
  observed: 1403,
  unit: 'assets',
  typical: 210,
  technical: { median: 210, mad: 40, window_runs: 12, seasonality: 'same_weekday', z: 29.8 },
  breakdown: [
    { key: 'Windows Events Scalyr', count: 631 },
    { key: 'SentinelOne', count: 402 },
  ],
  narrative,
};

describe('AlertEvidence', () => {
  it('shows a plain observed-vs-usual stats line without the z-score', () => {
    render(<AlertEvidence evidence={evidence} reason={narrative} />);
    const stats = screen.getByText(/Observed/);
    expect(stats.textContent).toContain('1403');
    expect(stats.textContent).toContain('210');
    expect(stats.textContent).not.toContain('z=29.8');
    expect(stats.textContent).not.toContain('29.8');
  });

  it('exposes the z-score only inside the collapsed Technical details JSON', () => {
    render(<AlertEvidence evidence={evidence} reason={narrative} />);
    expect(screen.getByText('Technical details')).toBeInTheDocument();
    // z never leaked into the primary stats <p> (checked above); confirm it's
    // still present somewhere — i.e. inside the collapsed raw-JSON <pre>.
    expect(document.body.textContent).toContain('29.8');
  });

  it('renders the breakdown table when present', () => {
    render(<AlertEvidence evidence={evidence} reason={narrative} />);
    expect(screen.getByText('Windows Events Scalyr')).toBeInTheDocument();
    expect(screen.getByText('631')).toBeInTheDocument();
  });

  it('renders the narrative when it differs from the reason shown above', () => {
    render(<AlertEvidence evidence={evidence} reason="A completely different reason." />);
    expect(screen.getByText(narrative)).toBeInTheDocument();
  });

  it('renders the narrative when no reason is passed', () => {
    render(<AlertEvidence evidence={evidence} />);
    expect(screen.getByText(narrative)).toBeInTheDocument();
  });

  it('suppresses the narrative when it duplicates the reason shown above (no triplication)', () => {
    render(<AlertEvidence evidence={evidence} reason={narrative} />);
    expect(screen.queryByText(narrative)).not.toBeInTheDocument();
  });

  it('degrades gracefully with a minimal evidence payload', () => {
    render(<AlertEvidence evidence={{ narrative: 'Something happened.' }} />);
    expect(screen.getByText('Something happened.')).toBeInTheDocument();
    expect(screen.getByText('Technical details')).toBeInTheDocument();
  });

  it('renders a labeled asset sample linking to asset-details', () => {
    render(<AlertEvidence evidence={{ observed: 500, sample: [
      { id: 'a1', label: 'web-01' }, { id: 'a2', label: 'web-02' }] }} />);
    expect(screen.getByText('web-01')).toHaveAttribute('href', '/asset-details?id=a1');
    expect(screen.getByText(/Sample of affected assets \(2 of 500\)/)).toBeInTheDocument();
  });
});
