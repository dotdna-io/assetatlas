// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';

import { type AlertChannel } from '@/api/channels';
import { SettingsSection } from '@/components/settings';
import styles from './SyslogChannelForm.module.css';

interface SyslogChannelFormProps {
  channel?: AlertChannel;
  onConfigChange: (config: Record<string, unknown>) => void;
}

const FACILITIES = [
  'kern', 'user', 'daemon', 'security', 'syslog',
  'local0', 'local1', 'local2', 'local3',
  'local4', 'local5', 'local6', 'local7',
];

interface FormState {
  host: string;
  portText: string; // kept as string for <input type="number">
  protocol: string;
  facility: string;
}

function configString(channel: AlertChannel | undefined, field: string, fallback: string): string {
  const v = channel?.config?.[field];
  return typeof v === 'string' && v ? v : fallback;
}

function configPort(channel: AlertChannel | undefined): string {
  const v = channel?.config?.port;
  return typeof v === 'number' ? String(v) : '514';
}

// Number('') is 0 (finite), so a blank field can't be told from an explicit 0
// by Number.isFinite alone; treat blank as the 514 default rather than coercing.
function parsePort(text: string): number {
  const n = Number(text);
  return text.trim() !== '' && Number.isFinite(n) ? n : 514;
}

function initialFormState(channel: AlertChannel | undefined): FormState {
  return {
    host: configString(channel, 'host', ''),
    portText: configPort(channel),
    protocol: configString(channel, 'protocol', 'udp'),
    facility: configString(channel, 'facility', 'local0'),
  };
}

export function SyslogChannelForm({ channel, onConfigChange }: SyslogChannelFormProps) {
  const [form, setForm] = useState<FormState>(() => initialFormState(channel));

  function update(patch: Partial<FormState>) {
    const next = { ...form, ...patch };
    setForm(next);
    onConfigChange({
      host: next.host.trim(),
      port: parsePort(next.portText),
      protocol: next.protocol,
      facility: next.facility,
    });
  }

  return (
    <SettingsSection title="Syslog configuration">
      <div className={styles.cardBody}>
        <div className={styles.row}>
          <label htmlFor="syslog_host">Host</label>
          <input
            id="syslog_host"
            value={form.host}
            onChange={(e) => update({ host: e.target.value })}
            placeholder="syslog.example.com"
          />
        </div>

        <div className={styles.row}>
          <label htmlFor="syslog_port">Port</label>
          <input
            id="syslog_port"
            type="number"
            value={form.portText}
            onChange={(e) => update({ portText: e.target.value })}
          />
        </div>

        <div className={styles.row}>
          <label htmlFor="syslog_protocol">Protocol</label>
          <select
            id="syslog_protocol"
            value={form.protocol}
            onChange={(e) => update({ protocol: e.target.value })}
          >
            <option value="udp">UDP</option>
            <option value="tcp">TCP</option>
          </select>
        </div>

        <div className={styles.row}>
          <label htmlFor="syslog_facility">Facility</label>
          <select
            id="syslog_facility"
            value={form.facility}
            onChange={(e) => update({ facility: e.target.value })}
          >
            {FACILITIES.map((f) => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
        </div>
      </div>
    </SettingsSection>
  );
}
