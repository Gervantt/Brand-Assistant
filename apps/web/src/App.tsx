import { Center, Loader } from '@mantine/core';
import { Suspense, lazy } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';

import { can } from './api/types';
import { useAuth } from './auth/AuthContext';
import { ClientProvider } from './auth/ClientContext';
import { WakeBanner } from './components/WakeBanner';
import { ChatPage } from './pages/ChatPage';
import { LoginPage } from './pages/LoginPage';

// Secondary pages are split out of the main bundle.
const DocumentsPage = lazy(() =>
  import('./pages/DocumentsPage').then((m) => ({ default: m.DocumentsPage })),
);
const PlansPage = lazy(() => import('./pages/PlansPage').then((m) => ({ default: m.PlansPage })));
const MetricsPage = lazy(() =>
  import('./pages/MetricsPage').then((m) => ({ default: m.MetricsPage })),
);

const spinner = (
  <Center h="100vh">
    <Loader />
  </Center>
);

function AppRoutes() {
  const { user, loading } = useAuth();
  if (loading) return spinner;
  if (!user) return <LoginPage />;
  return (
    <ClientProvider>
      <Suspense fallback={spinner}>
        <Routes>
        <Route path="/" element={<ChatPage />} />
        <Route path="/documents" element={<DocumentsPage />} />
        <Route path="/plans" element={<PlansPage />} />
        {can.admin(user.role) && <Route path="/metrics" element={<MetricsPage />} />}
        <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </ClientProvider>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <WakeBanner />
      <AppRoutes />
    </BrowserRouter>
  );
}
