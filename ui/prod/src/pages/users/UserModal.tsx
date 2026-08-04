// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, FormEvent } from 'react';
import type { User, CreateUserRequest, UpdateUserRequest } from '@/api/auth';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import { PasswordStrengthMeter, getRequirements } from '@/components/shared/PasswordStrengthMeter';
import { getInitials, getAvatarColor } from './user-utils';
import { EyeIcon } from '@/components/ui/EyeIcon';
import styles from './users.module.css';

type UserModalMode = 'create' | 'edit';

interface UserModalProps {
  isOpen: boolean;
  onClose: () => void;
  mode: UserModalMode;
  user?: User | null;
  onSubmit: (data: CreateUserRequest | UpdateUserRequest) => void;
  isSubmitting: boolean;
}

interface FormErrors {
  username?: string;
  email?: string;
  password?: string;
}

export function UserModal({
  isOpen,
  onClose,
  mode,
  user,
  onSubmit,
  isSubmitting,
}: UserModalProps) {
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [fullName, setFullName] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [role, setRole] = useState('user');
  const [inviteOnly, setInviteOnly] = useState(true);
  const [errors, setErrors] = useState<FormErrors>({});

  useEffect(() => {
    if (!isOpen) return;
    if (mode === 'edit' && user) {
      setUsername(user.username);
      setEmail(user.email);
      setFullName(user.full_name ?? '');
      setRole(user.role);
    } else {
      setUsername('');
      setEmail('');
      setFullName('');
      setPassword('');
      setRole('user');
      setInviteOnly(true);
    }
    setShowPassword(false);
    setErrors({});
  }, [isOpen, mode, user]);

  function validate(): boolean {
    const newErrors: FormErrors = {};
    if (mode === 'create') {
      if (!username.trim()) newErrors.username = 'Username is required';
      else if (/\s/.test(username)) newErrors.username = 'Username cannot contain spaces';
      if (!inviteOnly) {
        if (!password) newErrors.password = 'Password is required';
        else {
          const reqs = getRequirements(password);
          if (reqs.some((r) => !r.met)) newErrors.password = 'Password does not meet all requirements';
        }
      }
    }
    if (!email.trim()) newErrors.email = 'Email is required';
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) newErrors.email = 'Invalid email format';
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!validate()) return;

    if (mode === 'create') {
      const data: CreateUserRequest = {
        username: username.trim(),
        email: email.trim(),
        role,
        invite_only: inviteOnly,
      };
      if (!inviteOnly) data.password = password;
      if (fullName.trim()) data.full_name = fullName.trim();
      onSubmit(data);
    } else {
      const data: UpdateUserRequest = {
        email: email.trim(),
        full_name: fullName.trim() || undefined,
        role,
      };
      onSubmit(data);
    }
  }

  const title = mode === 'create' ? 'Create User' : 'Edit User';

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={title} size="sm">
      {mode === 'edit' && user && (
        <div className={styles.modalUserHeader}>
          <div className={styles.avatar} style={{ backgroundColor: getAvatarColor(user), width: 28, height: 28, fontSize: 11 }}>
            {getInitials(user)}
          </div>
          <span className={styles.modalUsername}>{user.username}</span>
        </div>
      )}
      <form onSubmit={handleSubmit}>
        <div className={styles.formGroup}>
          <label className={styles.formLabel}>
            Username {mode === 'create' && <span className={styles.required}>*</span>}
          </label>
          <input
            type="text"
            className={`${styles.formInput} ${errors.username ? styles.formInputError : ''}`}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            onBlur={() => {
              if (mode === 'create' && !username.trim()) setErrors((prev) => ({ ...prev, username: 'Username is required' }));
              else setErrors((prev) => { const { username: _, ...rest } = prev; return rest; });
            }}
            disabled={mode === 'edit'}
            placeholder="e.g. jsmith"
          />
          {mode === 'edit' && <span className={styles.hint}>Username cannot be changed</span>}
          {errors.username && <span className={styles.fieldError}>{errors.username}</span>}
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>
            Email <span className={styles.required}>*</span>
          </label>
          <input
            type="email"
            className={`${styles.formInput} ${errors.email ? styles.formInputError : ''}`}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onBlur={() => {
              if (!email.trim()) setErrors((prev) => ({ ...prev, email: 'Email is required' }));
              else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) setErrors((prev) => ({ ...prev, email: 'Invalid email format' }));
              else setErrors((prev) => { const { email: _, ...rest } = prev; return rest; });
            }}
            placeholder="e.g. jsmith@company.com"
          />
          {errors.email && <span className={styles.fieldError}>{errors.email}</span>}
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Full Name</label>
          <input
            type="text"
            className={styles.formInput}
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="e.g. Jane Smith"
          />
        </div>

        {mode === 'create' && (
          <div className={styles.formGroup}>
            <ToggleSwitch
              checked={inviteOnly}
              onChange={setInviteOnly}
              label="Send onboarding invite (user sets their own password)"
            />
          </div>
        )}

        {mode === 'create' && !inviteOnly && (
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>
              Password <span className={styles.required}>*</span>
            </label>
            <div className={styles.passwordWrapper}>
              <input
                type={showPassword ? 'text' : 'password'}
                className={`${styles.formInput} ${errors.password ? styles.formInputError : ''}`}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Minimum 8 characters"
              />
              <button
                type="button"
                className={styles.passwordToggle}
                onClick={() => setShowPassword((v) => !v)}
                title={showPassword ? 'Hide password' : 'Show password'}
              >
                <EyeIcon size={16} open={!showPassword} />
              </button>
            </div>
            {errors.password && <span className={styles.fieldError}>{errors.password}</span>}
            <PasswordStrengthMeter password={password} />
          </div>
        )}

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Role</label>
          <select
            className={styles.formInput}
            value={role}
            onChange={(e) => setRole(e.target.value)}
          >
            <option value="user">User</option>
            <option value="admin">Admin</option>
            <option value="super_admin">Super Admin</option>
          </select>
        </div>

        <div className={styles.modalFooter}>
          <Button variant="secondary" onClick={onClose} type="button">
            Cancel
          </Button>
          <Button variant="primary" type="submit" loading={isSubmitting}>
            {mode === 'create' ? 'Create User' : 'Save Changes'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
