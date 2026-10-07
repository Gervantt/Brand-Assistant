import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { api } from '../api/client';
import type { ClientSummary } from '../api/types';
import { useAuth } from './AuthContext';

const CLIENT_KEY = 'brand-assistant.client';

interface ClientState {
  clients: ClientSummary[];
  current: ClientSummary | null;
  select: (id: string) => void;
}

const ClientContext = createContext<ClientState | null>(null);

/** The client (brand) the user is working on; only clients they can access are listed. */
export function ClientProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [clients, setClients] = useState<ClientSummary[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(() => localStorage.getItem(CLIENT_KEY));

  useEffect(() => {
    if (!user) return;
    api<ClientSummary[]>('/clients')
      .then(setClients)
      .catch(() => setClients([]));
  }, [user]);

  const value = useMemo<ClientState>(() => {
    const current = clients.find((c) => c.id === currentId) ?? clients[0] ?? null;
    return {
      clients,
      current,
      select: (id) => {
        localStorage.setItem(CLIENT_KEY, id);
        setCurrentId(id);
      },
    };
  }, [clients, currentId]);

  return <ClientContext.Provider value={value}>{children}</ClientContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useClients(): ClientState {
  const context = useContext(ClientContext);
  if (!context) throw new Error('useClients must be used inside ClientProvider');
  return context;
}
