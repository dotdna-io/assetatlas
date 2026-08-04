// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { AlertSubjects } from './AlertSubjects';

describe('AlertSubjects', () => {
  it('renders a Data Sources list for integration subjects', () => {
    render(<AlertSubjects subjectType="integration"
      subjects={[{ id: 'i1', label: 'CrowdStrike', sublabel: 'EDR · sync failed' }]} />);
    expect(screen.getByText('Data Sources')).toBeInTheDocument();
    expect(screen.getByText('CrowdStrike')).toBeInTheDocument();
    expect(screen.getByText('EDR · sync failed')).toBeInTheDocument();
  });

  it('renders nothing for summary-only subject types', () => {
    const { container } = render(<AlertSubjects subjectType="pipeline_run" subjects={null} />);
    expect(container.firstChild).toBeNull();
  });
});
