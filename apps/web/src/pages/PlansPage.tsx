import { Accordion, Badge, Group, Stack, Text, Title } from '@mantine/core';
import { useEffect, useState } from 'react';

import { api } from '../api/client';
import type { ApprovedPlan } from '../api/types';
import { useClients } from '../auth/ClientContext';
import { ContentPlanTable } from '../components/artifacts';
import { Layout } from '../components/Layout';

export function PlansPage() {
  const { current } = useClients();
  const [plans, setPlans] = useState<ApprovedPlan[]>([]);

  useEffect(() => {
    if (!current) return;
    api<ApprovedPlan[]>(`/clients/${current.id}/plans`)
      .then(setPlans)
      .catch(() => setPlans([]));
  }, [current]);

  return (
    <Layout>
      <Stack maw={1100} mx="auto">
        <Title order={3}>Утверждённые контент-планы {current ? `— ${current.name}` : ''}</Title>
        {plans.length === 0 ? (
          <Text c="dimmed">Пока ничего не опубликовано. Менеджер публикует план кнопкой в чате.</Text>
        ) : (
          <Accordion variant="separated" radius="md">
            {plans.map((plan) => (
              <Accordion.Item key={plan.id} value={plan.id}>
                <Accordion.Control>
                  <Group justify="space-between" wrap="nowrap">
                    <div>
                      <Text fw={600}>{plan.title}</Text>
                      <Text size="xs" c="dimmed">
                        {plan.period_start} — {plan.period_end}
                      </Text>
                    </div>
                    <Badge color="green" variant="light">
                      {plan.payload.items.length} публикаций
                    </Badge>
                  </Group>
                </Accordion.Control>
                <Accordion.Panel>
                  <ContentPlanTable plan={plan.payload} />
                </Accordion.Panel>
              </Accordion.Item>
            ))}
          </Accordion>
        )}
      </Stack>
    </Layout>
  );
}
