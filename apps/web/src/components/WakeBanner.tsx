import { Alert, Loader } from '@mantine/core';
import { useSyncExternalStore } from 'react';

import { wakeStore } from '../api/client';

/** Free-tier backends (Render) sleep after inactivity; the first request takes ~30–50 s. */
export function WakeBanner() {
  const waking = useSyncExternalStore(wakeStore.subscribe, wakeStore.get);
  if (!waking) return null;
  return (
    <Alert
      color="yellow"
      radius={0}
      icon={<Loader size="xs" color="yellow" />}
      style={{ position: 'fixed', top: 0, left: 0, right: 0, zIndex: 1000 }}
    >
      Сервер просыпается после простоя — это занимает 30–50 секунд. Запрос выполнится
      автоматически.
    </Alert>
  );
}
