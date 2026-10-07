import { ActionIcon, AppShell, Badge, Burger, Button, Group, NavLink, Select, Stack, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconChartBar,
  IconFileText,
  IconLogout,
  IconMessages,
  IconTable,
} from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { NavLink as RouterLink, useLocation } from 'react-router-dom';

import { can } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { useClients } from '../auth/ClientContext';

const ROLE_LABELS = {
  viewer: 'Наблюдатель',
  copywriter: 'Копирайтер',
  manager: 'Менеджер',
  admin: 'Администратор',
} as const;

export function Layout({ sidebar, children }: { sidebar?: ReactNode; children: ReactNode }) {
  const { user, logout } = useAuth();
  const { clients, current, select } = useClients();
  const [opened, { toggle }] = useDisclosure();
  const location = useLocation();
  if (!user) return null;

  const links = [
    { to: '/', label: 'Чат', icon: IconMessages },
    { to: '/documents', label: 'Документы', icon: IconFileText },
    { to: '/plans', label: 'Утверждённые планы', icon: IconTable },
    ...(can.admin(user.role) ? [{ to: '/metrics', label: 'Метрики', icon: IconChartBar }] : []),
  ];

  return (
    <AppShell
      header={{ height: 56 }}
      navbar={{ width: 280, breakpoint: 'sm', collapsed: { mobile: !opened } }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Text fw={700} visibleFrom="sm">
              Brand Assistant
            </Text>
            <Select
              size="xs"
              w={170}
              placeholder={clients.length ? 'Клиент' : 'Нет доступных клиентов'}
              data={clients.map((c) => ({ value: c.id, label: c.name }))}
              value={current?.id ?? null}
              onChange={(id) => id && select(id)}
              allowDeselect={false}
              aria-label="Клиент"
            />
          </Group>
          <Group gap="xs" wrap="nowrap">
            <Text size="sm" visibleFrom="sm">
              {user.full_name || user.email}
            </Text>
            <Badge variant="light" visibleFrom="xs">
              {ROLE_LABELS[user.role]}
            </Badge>
            <Button
              size="xs"
              variant="subtle"
              leftSection={<IconLogout size={14} />}
              onClick={logout}
              visibleFrom="sm"
            >
              Выйти
            </Button>
            <ActionIcon variant="subtle" onClick={logout} hiddenFrom="sm" aria-label="Выйти">
              <IconLogout size={16} />
            </ActionIcon>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <Stack gap={4}>
          {links.map((link) => (
            <NavLink
              key={link.to}
              component={RouterLink}
              to={link.to}
              label={link.label}
              leftSection={<link.icon size={16} />}
              active={location.pathname === link.to}
            />
          ))}
        </Stack>
        {sidebar}
      </AppShell.Navbar>

      <AppShell.Main>{children}</AppShell.Main>
    </AppShell>
  );
}
