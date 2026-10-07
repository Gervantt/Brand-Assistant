import { ActionIcon, Alert, Badge, Card, Group, Stack, Table, Text, Title } from '@mantine/core';
import { Dropzone } from '@mantine/dropzone';
import { notifications } from '@mantine/notifications';
import { IconFileUpload, IconTrash } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { ApiError, api } from '../api/client';
import { can } from '../api/types';
import type { DocumentInfo } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { useClients } from '../auth/ClientContext';
import { Layout } from '../components/Layout';

const ACCEPT = {
  'application/pdf': ['.pdf'],
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
  'text/markdown': ['.md', '.markdown'],
  'text/plain': ['.txt', '.md'],
};
const MAX_BYTES = 10 * 1024 * 1024;

export function DocumentsPage() {
  const { user } = useAuth();
  const { current } = useClients();
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [version, setVersion] = useState(0);
  const reload = () => setVersion((v) => v + 1);

  useEffect(() => {
    if (!current) return;
    api<DocumentInfo[]>(`/clients/${current.id}/documents`)
      .then(setDocuments)
      .catch(() => setDocuments([]));
  }, [current, version]);

  const upload = async (files: File[]) => {
    if (!current) return;
    setUploading(true);
    for (const file of files) {
      const form = new FormData();
      form.append('file', file);
      try {
        const result = await api<{ title: string; chunks: number; duplicate: boolean }>(
          `/clients/${current.id}/documents`,
          { method: 'POST', form },
        );
        notifications.show({
          color: result.duplicate ? 'gray' : 'green',
          message: result.duplicate
            ? `«${result.title}» уже загружен`
            : `«${result.title}» загружен: ${result.chunks} фрагментов`,
        });
      } catch (error) {
        notifications.show({ color: 'red', message: error instanceof ApiError ? error.message : 'Ошибка загрузки' });
      }
    }
    setUploading(false);
    reload();
  };

  const remove = async (doc: DocumentInfo) => {
    if (!current || !window.confirm(`Удалить «${doc.title}» из базы знаний?`)) return;
    await api(`/clients/${current.id}/documents/${doc.id}`, { method: 'DELETE' });
    reload();
  };

  if (!user) return null;
  const canUpload = can.upload(user.role);

  return (
    <Layout>
      <Stack maw={900} mx="auto">
        <Title order={3}>Документы {current ? `— ${current.name}` : ''}</Title>
        <Text c="dimmed" size="sm">
          Брендбуки и брифы, по которым отвечает ассистент. Файлы разбиваются на фрагменты и
          индексируются для поиска.
        </Text>

        {canUpload ? (
          <Dropzone
            onDrop={(files) => void upload(files)}
            onReject={() => notifications.show({ color: 'red', message: 'Поддерживаются PDF, DOCX, MD и TXT до 10 МБ' })}
            accept={ACCEPT}
            maxSize={MAX_BYTES}
            loading={uploading}
            disabled={!current}
          >
            <Group justify="center" gap="md" mih={110} style={{ pointerEvents: 'none' }}>
              <IconFileUpload size={36} stroke={1.5} />
              <div>
                <Text>Перетащите брендбук или бриф сюда</Text>
                <Text size="sm" c="dimmed">
                  PDF, DOCX, Markdown или TXT, до 10 МБ
                </Text>
              </div>
            </Group>
          </Dropzone>
        ) : (
          <Alert color="gray">Загрузка документов доступна копирайтерам, менеджерам и администраторам.</Alert>
        )}

        <Card withBorder padding={0}>
          <Table verticalSpacing="sm" highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Документ</Table.Th>
                <Table.Th w={90}>Тип</Table.Th>
                <Table.Th w={110}>Фрагментов</Table.Th>
                <Table.Th w={120}>Загружен</Table.Th>
                {canUpload && <Table.Th w={50} />}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {documents.map((doc) => (
                <Table.Tr key={doc.id}>
                  <Table.Td>
                    <Text size="sm" fw={500}>
                      {doc.title}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {doc.filename} · {(doc.size_bytes / 1024).toFixed(0)} КБ
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light" tt="uppercase">
                      {doc.content_type}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{doc.chunk_count}</Table.Td>
                  <Table.Td>{new Date(doc.created_at).toLocaleDateString('ru-RU')}</Table.Td>
                  {canUpload && (
                    <Table.Td>
                      <ActionIcon variant="subtle" color="red" onClick={() => void remove(doc)} aria-label="Удалить">
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Table.Td>
                  )}
                </Table.Tr>
              ))}
              {documents.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={5}>
                    <Text c="dimmed" ta="center" size="sm" py="md">
                      Документов пока нет
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Card>
      </Stack>
    </Layout>
  );
}
