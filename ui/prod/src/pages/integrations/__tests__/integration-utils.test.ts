// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { getScheduleDescription, getNestedValue } from '../integration-utils';

describe('getNestedValue', () => {
  it('reads a flat key that literally contains dots', () => {
    // SentinelOne PowerQuery returns columns like "device.hostname" as flat keys
    const record = { 'device.hostname': 'DESKTOP-01', serial: 123 };
    expect(getNestedValue(record, 'device.hostname')).toBe('DESKTOP-01');
  });

  it('reads a multi-segment flat dotted key', () => {
    const record = { 'tag.hs.lentity': 'prod-entity' };
    expect(getNestedValue(record, 'tag.hs.lentity')).toBe('prod-entity');
  });

  it('still traverses genuinely nested objects', () => {
    const record = { computer: { general: { name: 'MAC-01' } } };
    expect(getNestedValue(record, 'computer.general.name')).toBe('MAC-01');
  });

  it('prefers a literal dotted key over a nested path on collision', () => {
    const record = { 'device.hostname': 'FLAT-WINS', device: { hostname: 'NESTED-LOSES' } };
    expect(getNestedValue(record, 'device.hostname')).toBe('FLAT-WINS');
  });

  it('returns undefined for a missing nested path', () => {
    expect(getNestedValue({ a: 1 }, 'b.c')).toBeUndefined();
  });

  it('reads a simple top-level key', () => {
    expect(getNestedValue({ serial: 'ABC' }, 'serial')).toBe('ABC');
  });
});

describe('getScheduleDescription', () => {
  it('returns "No schedule" when cron is missing', () => {
    expect(getScheduleDescription(null)).toBe('No schedule');
    expect(getScheduleDescription(undefined)).toBe('No schedule');
    expect(getScheduleDescription('')).toBe('No schedule');
  });

  describe('shorthand aliases', () => {
    it('translates @hourly', () => {
      expect(getScheduleDescription('@hourly')).toBe('Hourly at :00');
    });
    it('translates @daily', () => {
      expect(getScheduleDescription('@daily')).toBe('Daily at 00:00');
    });
    it('translates @weekly', () => {
      expect(getScheduleDescription('@weekly')).toBe('Weekly Sun at 00:00');
    });
    it('translates @monthly', () => {
      expect(getScheduleDescription('@monthly')).toBe('Monthly on day 1 at 00:00');
    });
  });

  describe('step expressions in hour', () => {
    it('translates "0 */4 * * *" as "Every 4 hours"', () => {
      expect(getScheduleDescription('0 */4 * * *')).toBe('Every 4 hours');
    });
    it('translates "0 */6 * * *" as "Every 6 hours"', () => {
      expect(getScheduleDescription('0 */6 * * *')).toBe('Every 6 hours');
    });
    it('translates "0 */12 * * *" as "Every 12 hours"', () => {
      expect(getScheduleDescription('0 */12 * * *')).toBe('Every 12 hours');
    });
    it('translates "30 */2 * * *" with non-zero minute offset', () => {
      expect(getScheduleDescription('30 */2 * * *')).toBe('Every 2 hours at :30');
    });
  });

  describe('step expressions in minute', () => {
    it('translates "*/15 * * * *" as "Every 15 minutes"', () => {
      expect(getScheduleDescription('*/15 * * * *')).toBe('Every 15 minutes');
    });
    it('translates "*/5 * * * *" as "Every 5 minutes"', () => {
      expect(getScheduleDescription('*/5 * * * *')).toBe('Every 5 minutes');
    });
    it('translates "*/1 * * * *" as "Every minute"', () => {
      expect(getScheduleDescription('*/1 * * * *')).toBe('Every minute');
    });
  });

  describe('hourly variations', () => {
    it('translates "0 * * * *" as "Hourly at :00"', () => {
      expect(getScheduleDescription('0 * * * *')).toBe('Hourly at :00');
    });
    it('translates "30 * * * *" as "Hourly at :30"', () => {
      expect(getScheduleDescription('30 * * * *')).toBe('Hourly at :30');
    });
    it('translates "* * * * *" as "Every minute"', () => {
      expect(getScheduleDescription('* * * * *')).toBe('Every minute');
    });
  });

  describe('daily', () => {
    it('translates "0 0 * * *" as "Daily at 00:00"', () => {
      expect(getScheduleDescription('0 0 * * *')).toBe('Daily at 00:00');
    });
    it('translates "0 2 * * *" as "Daily at 02:00"', () => {
      expect(getScheduleDescription('0 2 * * *')).toBe('Daily at 02:00');
    });
    it('translates "30 14 * * *" as "Daily at 14:30"', () => {
      expect(getScheduleDescription('30 14 * * *')).toBe('Daily at 14:30');
    });
  });

  describe('weekly', () => {
    it('translates "0 9 * * 1" as "Weekly Mon at 09:00"', () => {
      expect(getScheduleDescription('0 9 * * 1')).toBe('Weekly Mon at 09:00');
    });
    it('translates "0 0 * * 0" as "Weekly Sun at 00:00"', () => {
      expect(getScheduleDescription('0 0 * * 0')).toBe('Weekly Sun at 00:00');
    });
    it('translates weekdays "0 9 * * 1-5"', () => {
      expect(getScheduleDescription('0 9 * * 1-5')).toBe('Weekdays at 09:00');
    });
    it('translates weekends "0 9 * * 0,6"', () => {
      expect(getScheduleDescription('0 9 * * 0,6')).toBe('Weekends at 09:00');
    });
  });

  describe('monthly', () => {
    it('translates "0 0 1 * *" as "Monthly on day 1 at 00:00"', () => {
      expect(getScheduleDescription('0 0 1 * *')).toBe('Monthly on day 1 at 00:00');
    });
    it('translates "30 6 15 * *" as "Monthly on day 15 at 06:30"', () => {
      expect(getScheduleDescription('30 6 15 * *')).toBe('Monthly on day 15 at 06:30');
    });
  });

  describe('unsupported / custom', () => {
    it('falls back to Custom for malformed cron', () => {
      expect(getScheduleDescription('0 0 0')).toBe('Custom (0 0 0)');
    });
    it('falls back to Custom for complex multi-field expressions', () => {
      expect(getScheduleDescription('0 0 1,15 * *')).toBe('Custom (0 0 1,15 * *)');
    });
  });
});
