import {
  ActionIcon,
  Box,
  Button,
  Center,
  Divider,
  Group,
  NavLink,
  ScrollArea,
  Stack,
  Text,
  Textarea,
  Title,
} from '@mantine/core';
import { IconPlayerStopFilled, IconPlus, IconSend } from '@tabler/icons-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { streamMessage } from '../api/chat';
import { ApiError, api } from '../api/client';
import { can } from '../api/types';
import type { Conversation, ConversationDetail, Role } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { useClients } from '../auth/ClientContext';
import { applyEvent, newTurn, turnsFromHistory } from '../chat/turn';
import type { Turn } from '../chat/turn';
import { Layout } from '../components/Layout';
import { TurnView } from '../components/TurnView';

const SUGGESTIONS: Record<'ask' | 'generate', string[]> = {
  ask: [
    'Какой тон голоса у бренда?',
    'Какие фирменные цвета и шрифты?',
    'Что нельзя упоминать в публикациях?',
  ],
  generate: [
    'Составь контент-план на неделю для Instagram и Telegram',
    'Напиши пост про сезонное меню',
    'Подготовь бриф для дизайнера к этому посту',
  ],
};

function suggestionsFor(role: Role): string[] {
  return can.generate(role) ? [...SUGGESTIONS.ask.slice(0, 1), ...SUGGESTIONS.generate] : SUGGESTIONS.ask;
}

/** Remounted per client (key), so switching brands starts from a clean state. */
export function ChatPage() {
  const { current } = useClients();
  return <ChatView key={current?.id ?? 'none'} />;
}

function ChatView() {
  const { user } = useAuth();
  const { current } = useClients();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const loadConversations = useCallback(async () => {
    if (!current) return;
    setConversations(await api<Conversation[]>(`/conversations?client_id=${current.id}`));
  }, [current]);

  useEffect(() => {
    if (!current) return;
    api<Conversation[]>(`/conversations?client_id=${current.id}`)
      .then(setConversations)
      .catch(() => setConversations([]));
  }, [current]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [turns]);

  const open = async (id: string) => {
    if (busy) return;
    setConversationId(id);
    const detail = await api<ConversationDetail>(`/conversations/${id}`);
    setTurns(turnsFromHistory(detail.messages));
  };

  const updateTurn = (id: string, update: (turn: Turn) => Turn) =>
    setTurns((all) => all.map((turn) => (turn.id === id ? update(turn) : turn)));

  const send = async (text: string) => {
    const content = text.trim();
    if (!content || !current || busy) return;
    setInput('');
    setBusy(true);
    const turnId = crypto.randomUUID();
    setTurns((all) => [...all, newTurn(turnId, content)]);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      let id = conversationId;
      if (!id) {
        const created = await api<Conversation>('/conversations', {
          method: 'POST',
          json: { client_id: current.id },
        });
        id = created.id;
        setConversationId(id);
      }
      await streamMessage(id, content, (event) => updateTurn(turnId, (t) => applyEvent(t, event)), controller.signal);
      updateTurn(turnId, (t) => (t.done ? t : { ...t, status: null, done: true }));
      void loadConversations();
    } catch (error) {
      const message = error instanceof ApiError ? error.message : 'Не удалось отправить сообщение';
      updateTurn(turnId, (t) => ({ ...t, status: null, error: message, done: true }));
    } finally {
      abortRef.current = null;
      setBusy(false);
    }
  };

  const stop = () => abortRef.current?.abort();

  const startNew = () => {
    if (busy) return;
    setConversationId(null);
    setTurns([]);
  };

  if (!user) return null;

  const sidebar = (
    <>
      <Divider my="sm" />
      <Button
        variant="light"
        size="xs"
        leftSection={<IconPlus size={14} />}
        onClick={startNew}
        disabled={!current}
        mb="xs"
      >
        Новый диалог
      </Button>
      <ScrollArea style={{ flex: 1 }}>
        {conversations.map((conversation) => (
          <NavLink
            key={conversation.id}
            label={conversation.title || 'Без названия'}
            description={new Date(conversation.updated_at).toLocaleString('ru-RU', {
              day: 'numeric',
              month: 'short',
              hour: '2-digit',
              minute: '2-digit',
            })}
            active={conversation.id === conversationId}
            onClick={() => void open(conversation.id)}
          />
        ))}
      </ScrollArea>
    </>
  );

  return (
    <Layout sidebar={sidebar}>
      <Stack h="calc(100vh - 56px - 2 * var(--mantine-spacing-md))" gap="sm">
        <ScrollArea style={{ flex: 1 }} offsetScrollbars scrollbars="y">
          <Box maw={900} mx="auto">
            {!current ? (
              <Center h={300}>
                <Text c="dimmed">Нет доступных клиентов. Обратитесь к администратору.</Text>
              </Center>
            ) : turns.length === 0 ? (
              <Center mih={300}>
                <Stack align="center" gap="sm">
                  <Title order={4}>{current.name}</Title>
                  <Text c="dimmed" size="sm" ta="center">
                    Спросите о брендбуке{can.generate(user.role) ? ' или попросите подготовить контент' : ''}.
                  </Text>
                  <Group justify="center" gap="xs">
                    {suggestionsFor(user.role).map((suggestion) => (
                      <Button
                        key={suggestion}
                        variant="default"
                        size="xs"
                        h="auto"
                        py={6}
                        styles={{ label: { whiteSpace: 'normal' } }}
                        onClick={() => void send(suggestion)}
                      >
                        {suggestion}
                      </Button>
                    ))}
                  </Group>
                </Stack>
              </Center>
            ) : (
              <Stack gap="lg" py="sm">
                {turns.map((turn) => (
                  <TurnView key={turn.id} turn={turn} clientId={current.id} canPublish={can.publish(user.role)} />
                ))}
                <div ref={bottomRef} />
              </Stack>
            )}
          </Box>
        </ScrollArea>

        <Box maw={900} w="100%" mx="auto">
          <Group align="flex-end" gap="xs" wrap="nowrap">
            <Textarea
              style={{ flex: 1 }}
              autosize
              minRows={1}
              maxRows={6}
              placeholder={current ? `Сообщение для ${current.name}…` : 'Выберите клиента'}
              value={input}
              disabled={!current}
              onChange={(e) => setInput(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  void send(input);
                }
              }}
            />
            {busy ? (
              <ActionIcon size="lg" color="red" variant="light" onClick={stop} aria-label="Остановить">
                <IconPlayerStopFilled size={16} />
              </ActionIcon>
            ) : (
              <ActionIcon size="lg" onClick={() => void send(input)} disabled={!input.trim() || !current} aria-label="Отправить">
                <IconSend size={16} />
              </ActionIcon>
            )}
          </Group>
          <Text size="xs" c="dimmed" mt={4}>
            Enter — отправить, Shift+Enter — новая строка. Ответы основаны на брендбуке клиента.
          </Text>
        </Box>
      </Stack>
    </Layout>
  );
}
