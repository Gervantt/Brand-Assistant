import { fetchEventSource } from '@microsoft/fetch-event-source';

import { API_URL, ApiError, authHeaders, errorMessage, wakeStore } from './client';
import type { Artifact, BrandbookHit } from './types';

/** SSE events emitted by POST /conversations/{id}/messages (see backend agent/events.py). */
export type ChatEvent =
  | { type: 'meta'; data: { conversation_id: string; trace_id: string; tier: string } }
  | { type: 'status'; data: { text: string } }
  | { type: 'token'; data: { text: string } }
  | { type: 'tool_start'; data: { id: string; name: string; label: string } }
  | { type: 'tool_end'; data: { id: string; name: string; ok: boolean; summary: string } }
  | { type: 'artifact'; data: Artifact }
  | { type: 'citations'; data: { items: BrandbookHit[]; confidence: number; found: boolean } }
  | {
      type: 'confidence';
      data: { retrieval: number; self_assessed: number | null; combined: number; low: boolean };
    }
  | { type: 'error'; data: { message: string; code: string } }
  | { type: 'done'; data: { steps: number } };

class FatalError extends Error {}

export async function streamMessage(
  conversationId: string,
  content: string,
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const slowTimer = setTimeout(() => wakeStore.set(true), 5000);
  try {
    await fetchEventSource(`${API_URL}/conversations/${conversationId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ content }),
      signal,
      openWhenHidden: true,
      async onopen(response) {
        clearTimeout(slowTimer);
        wakeStore.set(false);
        if (!response.ok) throw new ApiError(response.status, await errorMessage(response));
      },
      onmessage(message) {
        if (!message.event || !message.data) return; // keep-alive pings
        if (message.event === 'artifact') {
          const data = JSON.parse(message.data) as { kind: Artifact['kind']; data: unknown };
          onEvent({ type: 'artifact', data: data as Artifact });
          return;
        }
        onEvent({ type: message.event, data: JSON.parse(message.data) } as ChatEvent);
      },
      onclose() {
        throw new FatalError('closed'); // never auto-reconnect: a retry would re-run the agent
      },
      onerror(error) {
        throw error; // rethrow = no retries
      },
    });
  } catch (error) {
    if (error instanceof FatalError || signal.aborted) return;
    if (error instanceof ApiError) throw error;
    wakeStore.set(true);
    throw new ApiError(0, 'Соединение прервалось. Проверьте сеть и попробуйте ещё раз.');
  } finally {
    clearTimeout(slowTimer);
  }
}
