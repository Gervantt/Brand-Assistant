import {
  Alert,
  Button,
  Center,
  Divider,
  Paper,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';

import { ApiError, api } from '../api/client';
import type { DemoAccount, Role } from '../api/types';
import { useAuth } from '../auth/AuthContext';

const ROLE_BUTTONS: Record<Role, string> = {
  viewer: 'Войти как наблюдатель',
  copywriter: 'Войти как копирайтер',
  manager: 'Войти как менеджер',
  admin: 'Войти как администратор',
};

export function LoginPage() {
  const { login, demoLogin } = useAuth();
  const [demo, setDemo] = useState<DemoAccount[]>([]);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api<DemoAccount[]>('/auth/demo-accounts')
      .then(setDemo)
      .catch(() => setDemo([]));
  }, []);

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Не удалось войти');
    } finally {
      setBusy(null);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void run('form', () => login(email, password));
  };

  return (
    <Center mih="100vh" p="md" bg="var(--mantine-color-gray-0)">
      <Paper withBorder radius="md" p="xl" w={420} maw="100%">
        <Title order={3}>Brand Assistant</Title>
        <Text c="dimmed" size="sm" mb="lg">
          Ассистент креативного агентства: брендбуки, контент-планы, посты и брифы.
        </Text>

        {demo.length > 0 && (
          <Stack gap="xs" mb="md">
            <Text size="sm" fw={600}>
              Демо-доступ
            </Text>
            {demo.map((account) => (
              <Button
                key={account.role}
                variant="light"
                justify="flex-start"
                h="auto"
                py={8}
                loading={busy === account.role}
                onClick={() => void run(account.role, () => demoLogin(account.role))}
                styles={{ label: { whiteSpace: 'normal', textAlign: 'left' } }}
              >
                <div>
                  <Text size="sm" fw={600}>
                    {ROLE_BUTTONS[account.role]}
                  </Text>
                  <Text size="xs" c="dimmed" fw={400}>
                    {account.description}
                  </Text>
                </div>
              </Button>
            ))}
            <Divider label="или по email" labelPosition="center" my="xs" />
          </Stack>
        )}

        <form onSubmit={submit}>
          <Stack gap="sm">
            <TextInput
              label="Email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.currentTarget.value)}
            />
            <PasswordInput
              label="Пароль"
              required
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
            />
            {error && <Alert color="red">{error}</Alert>}
            <Button type="submit" loading={busy === 'form'}>
              Войти
            </Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  );
}
