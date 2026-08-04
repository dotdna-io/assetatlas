// SPDX-License-Identifier: AGPL-3.0-or-later
import type { User } from '@/api/auth';

export function getInitials(user: User): string {
  if (user.full_name) {
    return user.full_name.split(' ').map((n) => n[0]).join('').toUpperCase().slice(0, 2);
  }
  return user.username.slice(0, 2).toUpperCase();
}

export function getAvatarColor(user: User): string {
  if (!user.is_active) return '#64748b';
  if (user.role === 'super_admin') return '#8b5cf6';
  if (user.role === 'admin') return '#f59e0b';
  return '#3b82f6';
}

export function formatRelativeTime(dateStr: string | null): string {
  if (!dateStr) return 'Never';
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHour / 24);

  if (diffSec < 60) return 'Just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHour < 24) return `${diffHour}h ago`;
  if (diffDay === 1) return 'Yesterday';
  if (diffDay < 30) return `${diffDay}d ago`;
  return date.toLocaleDateString();
}
