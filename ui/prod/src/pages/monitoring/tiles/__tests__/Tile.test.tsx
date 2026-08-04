// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { Tile } from '../Tile';

describe('Tile', () => {
  it('renders title + children when not loading and no error', () => {
    render(
      <Tile title="Request rate" isLoading={false} error={null} isEmpty={false} onRetry={vi.fn()}>
        <div data-testid="chart">chart</div>
      </Tile>,
    );
    expect(screen.getByText('Request rate')).toBeInTheDocument();
    expect(screen.getByTestId('chart')).toBeInTheDocument();
  });

  it('renders skeleton when isLoading', () => {
    render(
      <Tile title="Request rate" isLoading={true} error={null} isEmpty={false} onRetry={vi.fn()}>
        <div data-testid="chart">chart</div>
      </Tile>,
    );
    expect(screen.getByTestId('tile-skeleton')).toBeInTheDocument();
    expect(screen.queryByTestId('chart')).not.toBeInTheDocument();
  });

  it('renders error state with retry button when error is set', () => {
    const onRetry = vi.fn();
    render(
      <Tile
        title="Request rate"
        isLoading={false}
        error={new Error('boom')}
        isEmpty={false}
        onRetry={onRetry}
      >
        <div data-testid="chart">chart</div>
      </Tile>,
    );
    expect(screen.getByText(/Failed to load/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(onRetry).toHaveBeenCalled();
  });

  it('renders empty state when isEmpty', () => {
    render(
      <Tile title="Request rate" isLoading={false} error={null} isEmpty={true} onRetry={vi.fn()}>
        <div data-testid="chart">chart</div>
      </Tile>,
    );
    expect(screen.getByText(/No data for this range/i)).toBeInTheDocument();
    expect(screen.queryByTestId('chart')).not.toBeInTheDocument();
  });
});
