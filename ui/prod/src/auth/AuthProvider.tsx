// SPDX-License-Identifier: AGPL-3.0-or-later
import React, {
  createContext,
  useState,
  useEffect,
  useCallback,
  useMemo,
} from 'react';
import type { ReactNode } from 'react';
import type { User, LoginResult } from './auth-service';
import * as authService from './auth-service';

export interface AuthContextValue {
  user: User | null;
  isAuthenticated: boolean;
  isAdmin: boolean;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<LoginResult>;
  verifyMfaChallenge: (token: string, code: string) => Promise<User>;
  mfaEnrollSetup: (
    enrollmentToken: string,
  ) => Promise<{ secret: string; provisioning_uri: string }>;
  mfaEnrollVerify: (
    enrollmentToken: string,
    code: string,
  ) => Promise<{ user: User }>;
  exchangeOidcCode: (code: string) => Promise<User>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<User | null>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

interface AuthProviderProps {
  children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    authService.init();

    if (authService.isAuthenticated()) {
      setUser(authService.getCurrentUser());
    }

    setIsLoading(false);
  }, []);

  useEffect(() => {
    function handleStorageChange(event: StorageEvent) {
      if (event.key === 'access_token' && !event.newValue) {
        setUser(null);
      }
    }

    window.addEventListener('storage', handleStorageChange);
    return () => window.removeEventListener('storage', handleStorageChange);
  }, []);

  const login = useCallback(
    async (username: string, password: string): Promise<LoginResult> => {
      const result = await authService.login(username, password);
      if (result.kind === 'success') setUser(result.user);
      return result;
    },
    []
  );

  const verifyMfaChallenge = useCallback(
    async (token: string, code: string): Promise<User> => {
      const user = await authService.verifyMfaChallenge(token, code);
      setUser(user);
      return user;
    },
    []
  );

  const mfaEnrollSetup = useCallback(
    (enrollmentToken: string) =>
      authService.mfaEnrollSetup(enrollmentToken),
    [],
  );

  const mfaEnrollVerify = useCallback(
    async (enrollmentToken: string, code: string) => {
      const result = await authService.mfaEnrollVerify(enrollmentToken, code);
      setUser(result.user);
      return result;
    },
    [],
  );

  const exchangeOidcCode = useCallback(
    async (code: string): Promise<User> => {
      const user = await authService.exchangeOidcCode(code);
      setUser(user);
      return user;
    },
    []
  );

  const logout = useCallback(async (): Promise<void> => {
    await authService.logout();
  }, []);

  const refreshUser = useCallback(async (): Promise<User | null> => {
    // Best-effort: a transient /auth/me failure must not trap the caller in
    // a redirect loop after a successful self-service mutation.
    try {
      const fresh = await authService.fetchCurrentUser();
      if (fresh) {
        setUser(fresh);
      }
      return fresh;
    } catch (err) {
      console.warn('AuthContext refresh failed', err);
      return null;
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isAuthenticated: user !== null && authService.isAuthenticated(),
      isAdmin: authService.isAdmin(),
      isLoading,
      login,
      verifyMfaChallenge,
      mfaEnrollSetup,
      mfaEnrollVerify,
      exchangeOidcCode,
      logout,
      refreshUser,
    }),
    [
      user,
      isLoading,
      login,
      verifyMfaChallenge,
      mfaEnrollSetup,
      mfaEnrollVerify,
      exchangeOidcCode,
      logout,
      refreshUser,
    ],
  );

  return (
    <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
  );
}
