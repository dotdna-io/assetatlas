// SPDX-License-Identifier: AGPL-3.0-or-later
import styles from './PasswordStrengthMeter.module.css';

interface PasswordRequirement {
  label: string;
  met: boolean;
}

interface PasswordStrengthMeterProps {
  password: string;
}

// These rules must match the backend validator
// (src/auth/services/password_service.py::validate_password_strength) exactly.
// If they drift, the meter marks a password valid that the server then rejects
// with a 400 (regression: onboarding could not be completed, 2026-06-03).
function getRequirements(password: string): PasswordRequirement[] {
  return [
    { label: '8+ characters', met: password.length >= 8 },
    { label: 'Uppercase letter', met: /[A-Z]/.test(password) },
    { label: 'Lowercase letter', met: /[a-z]/.test(password) },
    { label: 'Number', met: /\d/.test(password) },
    { label: 'Special character', met: /[!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?]/.test(password) },
  ];
}

function getStrength(requirements: PasswordRequirement[]): { level: number; label: string } {
  const met = requirements.filter((r) => r.met).length;
  const missing = requirements.length - met;
  if (met === 0) return { level: 0, label: '' };
  // Only a complete set earns "Strong" (full green bar); each missing
  // requirement steps the meter down one level.
  if (missing === 0) return { level: 4, label: 'Strong' };
  if (missing === 1) return { level: 3, label: 'Medium' };
  if (missing === 2) return { level: 2, label: 'Fair' };
  return { level: 1, label: 'Weak' };
}

export function PasswordStrengthMeter({ password }: PasswordStrengthMeterProps) {
  const requirements = getRequirements(password);
  const { level, label } = getStrength(requirements);

  if (!password) return null;

  return (
    <div className={styles.meter}>
      <div className={styles.barRow}>
        <div className={styles.bar}>
          <div
            className={`${styles.barFill} ${styles[`fill${level}`]}`}
            style={{ width: `${(level / 4) * 100}%` }}
          />
        </div>
        {label && (
          <span className={`${styles.strengthLabel} ${styles[`label${level}`]}`}>
            {label}
          </span>
        )}
      </div>
      <div className={styles.requirements}>
        {requirements.map((req) => (
          <span key={req.label} className={req.met ? styles.met : styles.unmet}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              {req.met ? (
                <polyline points="20 6 9 17 4 12" />
              ) : (
                <>
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </>
              )}
            </svg>
            {req.label}
          </span>
        ))}
      </div>
    </div>
  );
}

export { getRequirements, getStrength };
