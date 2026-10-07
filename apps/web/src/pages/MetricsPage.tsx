import { Card, Group, SegmentedControl, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core';
import { useEffect, useState } from 'react';

import { api } from '../api/client';
import type { MetricsReport } from '../api/types';
import { Layout } from '../components/Layout';

const usd = (value: string) => `$${Number(value).toFixed(4)}`;
const pct = (value: number) => `${(value * 100).toFixed(1)}%`;
const ms = (value: number | null) => (value === null ? '—' : `${Math.round(value)} мс`);

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card withBorder padding="md">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
        {label}
      </Text>
      <Text size="xl" fw={700}>
        {value}
      </Text>
      {hint && (
        <Text size="xs" c="dimmed">
          {hint}
        </Text>
      )}
    </Card>
  );
}

export function MetricsPage() {
  const [hours, setHours] = useState('24');
  const [report, setReport] = useState<MetricsReport | null>(null);

  useEffect(() => {
    api<MetricsReport>(`/admin/metrics?hours=${hours}`)
      .then(setReport)
      .catch(() => setReport(null));
  }, [hours]);

  const totals = report?.totals;
  return (
    <Layout>
      <Stack maw={1100} mx="auto">
        <Group justify="space-between">
          <Title order={3}>Метрики LLM</Title>
          <SegmentedControl
            value={hours}
            onChange={setHours}
            data={[
              { value: '24', label: '24 часа' },
              { value: '168', label: '7 дней' },
              { value: '720', label: '30 дней' },
            ]}
          />
        </Group>
        {totals && report && (
          <>
            <SimpleGrid cols={{ base: 2, md: 4 }}>
              <Stat label="Вызовов" value={String(totals.calls)} hint={`${totals.tokens_in + totals.tokens_out} токенов`} />
              <Stat label="Стоимость" value={usd(totals.cost_usd)} hint={`сэкономлено кэшем ${usd(totals.cost_saved_usd)}`} />
              <Stat label="Latency" value={ms(totals.avg_latency_ms)} hint={`p95 ${ms(totals.p95_latency_ms)}`} />
              <Stat
                label="Ошибки / fallback"
                value={`${pct(totals.error_rate)} / ${pct(totals.fallback_rate)}`}
                hint={`попаданий в кэш: ${totals.cache_hits}`}
              />
            </SimpleGrid>

            <Card withBorder padding={0}>
              <Table verticalSpacing="xs" fz="sm">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Модель</Table.Th>
                    <Table.Th>Вызовов</Table.Th>
                    <Table.Th>Ошибки</Table.Th>
                    <Table.Th>Fallback</Table.Th>
                    <Table.Th>Avg / p95</Table.Th>
                    <Table.Th>Токены in/out</Table.Th>
                    <Table.Th>Стоимость</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {report.by_model.map((row) => (
                    <Table.Tr key={`${row.provider}/${row.model}`}>
                      <Table.Td>
                        {row.provider}/{row.model}
                      </Table.Td>
                      <Table.Td>{row.calls}</Table.Td>
                      <Table.Td>{pct(row.error_rate)}</Table.Td>
                      <Table.Td>{pct(row.fallback_rate)}</Table.Td>
                      <Table.Td>
                        {ms(row.avg_latency_ms)} / {ms(row.p95_latency_ms)}
                      </Table.Td>
                      <Table.Td>
                        {row.tokens_in} / {row.tokens_out}
                      </Table.Td>
                      <Table.Td>{usd(row.cost_usd)}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Card>

            <Card withBorder padding="md">
              <Text fw={600} mb="xs">
                Стоимость по дням
              </Text>
              {report.daily.map((day) => (
                <Group key={day.day} justify="space-between">
                  <Text size="sm">{day.day}</Text>
                  <Text size="sm">
                    {day.calls} вызовов · {usd(day.cost_usd)} (кэш сэкономил {usd(day.cost_saved_usd)})
                  </Text>
                </Group>
              ))}
            </Card>
          </>
        )}
      </Stack>
    </Layout>
  );
}
