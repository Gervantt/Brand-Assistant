import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { api, setAuth } from '../api/client';
import type { Role, TokenResponse, User } from '../api/types';

const TOKEN_KEY = 'brand-assistant.token';

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  demoLogin: (role: Role) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(() => localStorage.getItem(TOKEN_KEY) !== null);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setAuth(null);
    setUser(null);
  }, []);

  const accept = useCallback(
    (response: TokenResponse) => {
      localStorage.setItem(TOKEN_KEY, response.access_token);
      setAuth(response.access_token, logout);
      setUser(response.user);
    },
    [logout],
  );

  useEffect(() => {
    const saved = localStorage.getItem(TOKEN_KEY);
    if (!saved) return;
    setAuth(saved, logout);
    api<User>('/auth/me')
      .then(setUser)
      .catch(logout)
      .finally(() => setLoading(false));
  }, [logout]);

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      logout,
      login: async (email, password) =>
        accept(await api<TokenResponse>('/auth/login', { method: 'POST', json: { email, password } })),
      demoLogin: async (role) =>
        accept(await api<TokenResponse>('/auth/demo-login', { method: 'POST', json: { role } })),
    }),
    [user, loading, logout, accept],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside AuthProvider');
  return context;
}
