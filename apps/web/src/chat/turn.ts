import type { ChatEvent } from '../api/chat';
import type { Artifact, BrandbookHit, StoredMessage } from '../api/types';

export interface ToolStatus {
  id: string;
  name: string;
  label: string;
  state: 'running' | 'ok' | 'error';
  summary: string;
}

export interface Confidence {
  retrieval: number;
  self_assessed: number | null;
  combined: number;
  low: boolean;
}

/** One user message and the assistant's answer to it. */
export interface Turn {
  id: string;
  user: string;
  text: string;
  status: string | null;
  tools: ToolStatus[];
  artifacts: Artifact[];
  citations: BrandbookHit[];
  confidence: Confidence | null;
  error: string | null;
  done: boolean;
}

export function newTurn(id: string, user: string): Turn {
  return {
    id,
    user,
    text: '',
    status: 'Отправляю…',
    tools: [],
    artifacts: [],
    citations: [],
    confidence: null,
    error: null,
    done: false,
  };
}

export function applyEvent(turn: Turn, event: ChatEvent): Turn {
  switch (event.type) {
    case 'meta':
      return turn;
    case 'status':
      return { ...turn, status: event.data.text };
    case 'token':
      return { ...turn, status: null, text: turn.text + event.data.text };
    case 'tool_start':
      return {
        ...turn,
        status: event.data.label,
        tools: [
          ...turn.tools,
          { ...event.data, state: 'running', summary: '' },
        ],
      };
    case 'tool_end': {
      const { id, name, ok, summary } = event.data;
      const known = turn.tools.some((tool) => tool.id === id);
      const state: ToolStatus['state'] = ok ? 'ok' : 'error';
      const tools = known
        ? turn.tools.map((tool) => (tool.id === id ? { ...tool, state, summary } : tool))
        : [...turn.tools, { id, name, label: name, state, summary }]; // blocked before start
      return { ...turn, tools };
    }
    case 'artifact':
      return { ...turn, artifacts: [...turn.artifacts, event.data] };
    case 'citations':
      return { ...turn, citations: [...turn.citations, ...event.data.items] };
    case 'confidence':
      return { ...turn, confidence: event.data };
    case 'error':
      return { ...turn, status: null, error: event.data.message, done: true };
    case 'done':
      return { ...turn, status: null, done: true };
  }
}

/** Rebuild turns from the stored transcript of a conversation. */
export function turnsFromHistory(messages: StoredMessage[]): Turn[] {
  const turns: Turn[] = [];
  messages.forEach((message, index) => {
    if (message.role === 'user') {
      turns.push({ ...newTurn(`h${index}`, message.content), status: null, done: true });
      return;
    }
    const last = turns.at(-1);
    if (last) {
      last.text += message.content;
      last.artifacts.push(...message.artifacts);
    }
  });
  return turns;
}
