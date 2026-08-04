// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PasswordStrengthMeter, getRequirements, getStrength } from '../PasswordStrengthMeter';

// Must stay identical to the backend's special-char set
// (src/auth/services/password_service.py / tests/unit/test_password_service.py).
// If the meter accepts a character the backend rejects, onboarding 400s — which
// is what happened on 2026-06-03 with a '_'-only special character.
const BACKEND_SPECIAL_CHARS = "!@#$%^&*()_+-=[]{};':\"\\|,.<>/?";

const met = (password: string, label: string): boolean =>
  getRequirements(password).find((r) => r.label === label)!.met;

describe('PasswordStrengthMeter requirements', () => {
  it('checks lowercase as a distinct requirement (the backend enforces it too)', () => {
    const labels = getRequirements('').map((r) => r.label);
    expect(labels).toContain('Lowercase letter');
  });

  it('marks a password with no lowercase letter as not meeting all requirements', () => {
    // Has length, uppercase, digit and a special char, but no lowercase: the
    // backend would reject this, so the meter must not call it complete.
    expect(met('PASSWORD1!', 'Lowercase letter')).toBe(false);
    const all = getRequirements('PASSWORD1!');
    expect(all.every((r) => r.met)).toBe(false);
  });

  it.each(BACKEND_SPECIAL_CHARS.split(''))(
    "treats %j as a special character (parity with the backend)",
    (special) => {
      expect(met(`Passw0rd${special}`, 'Special character')).toBe(true);
    },
  );

  it('accepts an underscore-only special character (onboarding regression)', () => {
    const all = getRequirements('Password1_');
    expect(all.every((r) => r.met)).toBe(true);
  });
});

describe('PasswordStrengthMeter getStrength', () => {
  it('reports "Strong" only when every requirement is met', () => {
    const full = getStrength(getRequirements('Password1!'));
    expect(full).toEqual({ level: 4, label: 'Strong' });
  });

  it('does not report "Strong" when a single requirement is missing', () => {
    const missingLowercase = getStrength(getRequirements('PASSWORD1!'));
    expect(missingLowercase.label).not.toBe('Strong');
    expect(missingLowercase.level).toBeLessThan(4);
  });

  it('returns an empty label for an empty password', () => {
    expect(getStrength(getRequirements(''))).toEqual({ level: 0, label: '' });
  });
});

describe('PasswordStrengthMeter render', () => {
  it('renders the lowercase requirement to the user', () => {
    render(<PasswordStrengthMeter password="PASSWORD1!" />);
    expect(screen.getByText('Lowercase letter')).toBeInTheDocument();
  });
});
