// SPDX-License-Identifier: AGPL-3.0-or-later
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { SyslogChannelForm } from '../SyslogChannelForm';
import type { AlertChannel } from '@/api/channels';

describe('SyslogChannelForm', () => {
  it('seeds fields from channel.config', () => {
    render(
      <SyslogChannelForm
        channel={{
          id: 'c1', type: 'syslog', name: 'siem', enabled: true,
          config: { host: 'collector.x', port: 6514, protocol: 'tcp', facility: 'local3' },
        } as AlertChannel}
        onConfigChange={vi.fn()}
      />,
    );
    expect(screen.getByDisplayValue('collector.x')).toBeInTheDocument();
    expect(screen.getByDisplayValue('6514')).toBeInTheDocument();
    expect((screen.getByLabelText(/^protocol$/i) as HTMLSelectElement).value).toBe('tcp');
    expect((screen.getByLabelText(/^facility$/i) as HTMLSelectElement).value).toBe('local3');
  });

  it('emits full config when the host is edited', () => {
    const onConfigChange = vi.fn();
    render(<SyslogChannelForm onConfigChange={onConfigChange} />);
    fireEvent.change(screen.getByLabelText(/^host$/i), { target: { value: 'siem.corp' } });
    expect(onConfigChange).toHaveBeenLastCalledWith({
      host: 'siem.corp', port: 514, protocol: 'udp', facility: 'local0',
    });
  });

  it('emits the selected protocol and facility', () => {
    const onConfigChange = vi.fn();
    render(<SyslogChannelForm onConfigChange={onConfigChange} />);
    fireEvent.change(screen.getByLabelText(/^protocol$/i), { target: { value: 'tcp' } });
    fireEvent.change(screen.getByLabelText(/^facility$/i), { target: { value: 'local5' } });
    expect(onConfigChange).toHaveBeenLastCalledWith({
      host: '', port: 514, protocol: 'tcp', facility: 'local5',
    });
  });

  it('does not emit on mount (parent seeds config)', () => {
    const onConfigChange = vi.fn();
    render(<SyslogChannelForm onConfigChange={onConfigChange} />);
    expect(onConfigChange).not.toHaveBeenCalled();
  });
});
