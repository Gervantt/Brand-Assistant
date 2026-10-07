import {
  Alert,
  Badge,
  Button,
  Card,
  CopyButton,
  Group,
  List,
  ScrollArea,
  SimpleGrid,
  Spoiler,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconCopy, IconSend } from '@tabler/icons-react';
import { useState } from 'react';

import { ApiError, api } from '../api/client';
import type { Artifact, ContentPlan, DesignerBrief, Post, PublishResult } from '../api/types';

const FORMAT_LABELS: Record<string, string> = {
  post: 'Пост',
  carousel: 'Карусель',
  reels: 'Reels',
  stories: 'Сторис',
  video: 'Видео',
  article: 'Статья',
};

const formatLabel = (format: string) => FORMAT_LABELS[format] ?? format;
const ruDate = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', weekday: 'short' });

function Hashtags({ tags }: { tags: string[] }) {
  return (
    <Group gap={4}>
      {tags.map((tag) => (
        <Badge key={tag} variant="light" color="gray" size="sm" tt="none">
          {tag}
        </Badge>
      ))}
    </Group>
  );
}

export function ContentPlanTable({ plan }: { plan: ContentPlan }) {
  return (
    <ScrollArea>
      <Table striped verticalSpacing="xs" fz="sm" miw={760}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th w={110}>Дата</Table.Th>
            <Table.Th w={110}>Площадка</Table.Th>
            <Table.Th w={140}>Рубрика</Table.Th>
            <Table.Th>Публикация</Table.Th>
            <Table.Th w={220}>Визуал</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {plan.items.map((item, index) => (
            <Table.Tr key={`${item.date}-${index}`}>
              <Table.Td>{ruDate(item.date)}</Table.Td>
              <Table.Td>
                <Text size="sm">{item.platform}</Text>
                <Badge size="xs" variant="outline">
                  {formatLabel(item.format)}
                </Badge>
              </Table.Td>
              <Table.Td>{item.rubric}</Table.Td>
              <Table.Td>
                <Text fw={600} size="sm">
                  {item.title}
                </Text>
                <Spoiler
                  maxHeight={44}
                  showLabel="Показать текст"
                  hideLabel="Скрыть"
                  styles={{ control: { fontSize: 'var(--mantine-font-size-xs)' } }}
                >
                  <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                    {item.text}
                  </Text>
                </Spoiler>
                <Hashtags tags={item.hashtags} />
              </Table.Td>
              <Table.Td>
                <Text size="sm" c="dimmed">
                  {item.visual_idea}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </ScrollArea>
  );
}

function ContentPlanCard({
  draftId,
  plan,
  clientId,
  canPublish,
}: {
  draftId: string;
  plan: ContentPlan;
  clientId: string;
  canPublish: boolean;
}) {
  const [published, setPublished] = useState<PublishResult | null>(null);
  const [busy, setBusy] = useState(false);

  const publish = async () => {
    setBusy(true);
    try {
      const result = await api<PublishResult>('/plans/publish', {
        method: 'POST',
        json: { client_id: clientId, draft_id: draftId },
      });
      setPublished(result);
      notifications.show({ color: 'green', message: `План «${result.title}» опубликован` });
    } catch (error) {
      notifications.show({ color: 'red', message: error instanceof ApiError ? error.message : 'Ошибка' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card withBorder radius="md" padding="md">
      <Group justify="space-between" align="flex-start" mb="xs">
        <div>
          <Title order={5}>{plan.title}</Title>
          <Text size="sm" c="dimmed">
            {ruDate(plan.period_start)} — {ruDate(plan.period_end)} · {plan.items.length} публикаций ·{' '}
            {plan.goal}
          </Text>
        </div>
        {published ? (
          <Badge color="green" leftSection={<IconCheck size={12} />}>
            Опубликован
          </Badge>
        ) : canPublish ? (
          <Button size="xs" leftSection={<IconSend size={14} />} loading={busy} onClick={publish}>
            Опубликовать
          </Button>
        ) : (
          <Badge variant="light" color="gray">
            Черновик
          </Badge>
        )}
      </Group>
      <ContentPlanTable plan={plan} />
    </Card>
  );
}

function PostCard({ post }: { post: Post }) {
  const full = [post.title, '', post.text, '', post.call_to_action, post.hashtags.join(' ')].join('\n');
  return (
    <Card withBorder radius="md" padding="md">
      <Group justify="space-between" mb="xs">
        <Group gap="xs">
          <Badge>{post.platform}</Badge>
          <Badge variant="outline">{formatLabel(post.format)}</Badge>
        </Group>
        <CopyButton value={full}>
          {({ copied, copy }) => (
            <Button
              size="xs"
              variant="subtle"
              leftSection={copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
              onClick={copy}
            >
              {copied ? 'Скопировано' : 'Копировать'}
            </Button>
          )}
        </CopyButton>
      </Group>
      <Title order={5} mb={4}>
        {post.title}
      </Title>
      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }} mb="xs">
        {post.text}
      </Text>
      {post.call_to_action && (
        <Text size="sm" fw={600} mb="xs">
          {post.call_to_action}
        </Text>
      )}
      <Hashtags tags={post.hashtags} />
      <Text size="sm" c="dimmed" mt="sm">
        Визуал: {post.visual_idea}
      </Text>
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
        {label}
      </Text>
      <Text size="sm" component="div">
        {children}
      </Text>
    </div>
  );
}

function Bullets({ items }: { items: string[] }) {
  return (
    <List size="sm" spacing={2}>
      {items.map((item) => (
        <List.Item key={item}>{item}</List.Item>
      ))}
    </List>
  );
}

function BriefCard({ brief }: { brief: DesignerBrief }) {
  return (
    <Card withBorder radius="md" padding="md">
      <Group gap="xs" mb="xs">
        <Title order={5}>Бриф: {brief.title}</Title>
        <Badge>{brief.platform}</Badge>
        <Badge variant="outline">
          {formatLabel(brief.format)} · {brief.dimensions}
        </Badge>
      </Group>
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
        <Field label="Цель">{brief.objective}</Field>
        <Field label="Ключевое сообщение">{brief.key_message}</Field>
        <Field label="Текст на визуале">{brief.text_on_visual || '—'}</Field>
        <Field label="Настроение">{brief.mood.join(', ') || '—'}</Field>
        <Field label="Описание визуала">{brief.visual_description}</Field>
        <Field label="Цвета">
          <Group gap={6}>
            {brief.colors.map((color) => {
              const hex = /#[0-9a-f]{6}/i.exec(color)?.[0];
              return (
                <Badge
                  key={color}
                  variant="outline"
                  tt="none"
                  leftSection={
                    hex ? (
                      <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: hex }} />
                    ) : undefined
                  }
                >
                  {color}
                </Badge>
              );
            })}
          </Group>
        </Field>
        <Field label="Делать">
          <Bullets items={brief.do} />
        </Field>
        <Field label="Не делать">
          <Bullets items={brief.dont} />
        </Field>
        <Field label="Файлы на выходе">
          <Bullets items={brief.deliverables} />
        </Field>
      </SimpleGrid>
    </Card>
  );
}

export function ArtifactView({
  artifact,
  clientId,
  canPublish,
}: {
  artifact: Artifact;
  clientId: string;
  canPublish: boolean;
}) {
  switch (artifact.kind) {
    case 'content_plan':
      return (
        <ContentPlanCard
          draftId={artifact.data.draft_id}
          plan={artifact.data.plan}
          clientId={clientId}
          canPublish={canPublish}
        />
      );
    case 'post':
      return <PostCard post={artifact.data.post} />;
    case 'designer_brief':
      return <BriefCard brief={artifact.data.brief} />;
    case 'publication':
      return (
        <Alert color="green" icon={<IconCheck />} title="План опубликован">
          «{artifact.data.title}» — {artifact.data.items} публикаций
          {artifact.data.already_published ? ' (уже был опубликован ранее)' : ''}
        </Alert>
      );
  }
}

