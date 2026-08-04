// SPDX-License-Identifier: AGPL-3.0-or-later
import { useContext } from 'react';
import { AuthContext } from './AuthProvider';
import type { AuthContextValue } from './AuthProvider';

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
