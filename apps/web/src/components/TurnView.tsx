import { Accordion, Alert, Badge, Box, Group, Loader, Paper, Stack, Text, Tooltip } from '@mantine/core';
import { IconAlertTriangle, IconCheck, IconShieldCheck, IconX } from '@tabler/icons-react';
import Markdown from 'react-markdown';

import type { Turn } from '../chat/turn';
import { ArtifactView } from './artifacts';

function ConfidenceBadge({ turn }: { turn: Turn }) {
  const confidence = turn.confidence;
  if (!confidence) return null;
  const percent = Math.round(confidence.combined * 100);
  return (
    <Tooltip
      label={`Поиск: ${Math.round(confidence.retrieval * 100)}% · самооценка модели: ${
        confidence.self_assessed === null ? '—' : `${Math.round(confidence.self_assessed * 100)}%`
      }`}
    >
      {confidence.low ? (
        <Badge color="yellow" variant="light" leftSection={<IconAlertTriangle size={12} />}>
          Низкая уверенность · {percent}%
        </Badge>
      ) : (
        <Badge color="green" variant="light" leftSection={<IconShieldCheck size={12} />}>
          Подтверждено брендбуком · {percent}%
        </Badge>
      )}
    </Tooltip>
  );
}

export function TurnView({
  turn,
  clientId,
  canPublish,
}: {
  turn: Turn;
  clientId: string;
  canPublish: boolean;
}) {
  return (
    <Stack gap="xs">
      <Paper
        radius="lg"
        px="md"
        py="xs"
        bg="var(--mantine-color-blue-light)"
        style={{ alignSelf: 'flex-end', maxWidth: '80%' }}
      >
        <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
          {turn.user}
        </Text>
      </Paper>

      <Box style={{ maxWidth: '100%' }}>
        {turn.tools.length > 0 && (
          <Group gap={6} mb={6}>
            {turn.tools.map((tool) => (
              <Badge
                key={tool.id}
                variant="light"
                color={tool.state === 'error' ? 'red' : tool.state === 'ok' ? 'teal' : 'blue'}
                leftSection={
                  tool.state === 'running' ? (
                    <Loader size={10} />
                  ) : tool.state === 'ok' ? (
                    <IconCheck size={12} />
                  ) : (
                    <IconX size={12} />
                  )
                }
                tt="none"
                title={tool.summary || undefined}
              >
                {tool.label}
                {tool.state === 'error' && tool.summary ? ` — ${tool.summary}` : ''}
              </Badge>
            ))}
          </Group>
        )}

        {turn.text && (
          <Box fz="sm" className="markdown">
            <Markdown>{turn.text}</Markdown>
          </Box>
        )}

        {turn.status && !turn.done && (
          <Group gap="xs" c="dimmed">
            <Loader size="xs" type="dots" />
            <Text size="sm">{turn.status}</Text>
          </Group>
        )}

        {turn.error && (
          <Alert color="red" icon={<IconAlertTriangle />} mt="xs">
            {turn.error}
          </Alert>
        )}

        <Stack gap="sm" mt={turn.artifacts.length ? 'sm' : 0}>
          {turn.artifacts.map((artifact, index) => (
            <ArtifactView key={index} artifact={artifact} clientId={clientId} canPublish={canPublish} />
          ))}
        </Stack>

        {(turn.confidence || turn.citations.length > 0) && (
          <Stack gap={4} mt="xs">
            <ConfidenceBadge turn={turn} />
            {turn.citations.length > 0 && (
              <Accordion variant="contained" radius="md" chevronPosition="left">
                <Accordion.Item value="sources">
                  <Accordion.Control>
                    <Text size="sm">Источники ({turn.citations.length})</Text>
                  </Accordion.Control>
                  <Accordion.Panel>
                    <Stack gap="xs">
                      {turn.citations.map((hit, index) => (
                        <div key={index}>
                          <Text size="xs" fw={600}>
                            [{index + 1}] {hit.document}
                            {hit.section ? ` · ${hit.section}` : ''}{' '}
                            <Text span c="dimmed" size="xs">
                              ({Math.round(hit.score * 100)}%)
                            </Text>
                          </Text>
                          <Text size="xs" c="dimmed" lineClamp={4} style={{ whiteSpace: 'pre-wrap' }}>
                            {hit.text}
                          </Text>
                        </div>
                      ))}
                    </Stack>
                  </Accordion.Panel>
                </Accordion.Item>
              </Accordion>
            )}
          </Stack>
        )}
      </Box>
    </Stack>
  );
}
