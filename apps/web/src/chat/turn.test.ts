import { describe, expect, it } from 'vitest';

import type { ChatEvent } from '../api/chat';
import { applyEvent, newTurn, turnsFromHistory } from './turn';

const replay = (events: ChatEvent[]) => events.reduce(applyEvent, newTurn('t1', 'Какой тон?'));

describe('applyEvent', () => {
  it('streams tokens, tool statuses and citations into one turn', () => {
    const turn = replay([
      { type: 'meta', data: { conversation_id: 'c', trace_id: 't', tier: 'simple' } },
      { type: 'status', data: { text: 'Думаю…' } },
      { type: 'tool_start', data: { id: 'call_0', name: 'search_brandbook', label: 'Ищу в брендбуке…' } },
      {
        type: 'citations',
        data: {
          items: [{ document: 'Брендбук', section: 'Тон', text: 'Тёплый', score: 0.7 }],
          confidence: 0.7,
          found: true,
        },
      },
      { type: 'tool_end', data: { id: 'call_0', name: 'search_brandbook', ok: true, summary: '' } },
      { type: 'token', data: { text: 'Тёплый ' } },
      { type: 'token', data: { text: 'тон [1].' } },
      { type: 'confidence', data: { retrieval: 0.7, self_assessed: 0.9, combined: 0.78, low: false } },
      { type: 'done', data: { steps: 2 } },
    ]);
    expect(turn.text).toBe('Тёплый тон [1].');
    expect(turn.tools).toEqual([
      { id: 'call_0', name: 'search_brandbook', label: 'Ищу в брендбуке…', state: 'ok', summary: '' },
    ]);
    expect(turn.citations).toHaveLength(1);
    expect(turn.confidence?.low).toBe(false);
    expect(turn.done).toBe(true);
    expect(turn.status).toBeNull();
  });

  it('records tools blocked by the gateway (tool_end without tool_start)', () => {
    const turn = replay([
      { type: 'tool_end', data: { id: 'x', name: 'publish_content_plan', ok: false, summary: 'Нет доступа' } },
    ]);
    expect(turn.tools[0]?.state).toBe('error');
    expect(turn.tools[0]?.summary).toBe('Нет доступа');
  });

  it('stops on errors with a readable message', () => {
    const turn = replay([{ type: 'error', data: { message: 'Сервис недоступен', code: 'llm_unavailable' } }]);
    expect(turn.error).toBe('Сервис недоступен');
    expect(turn.done).toBe(true);
  });
});

describe('turnsFromHistory', () => {
  it('pairs user messages with the following assistant output', () => {
    const turns = turnsFromHistory([
      { role: 'user', content: 'Привет', artifacts: [], created_at: '' },
      { role: 'assistant', content: 'Здравствуйте!', artifacts: [], created_at: '' },
      { role: 'user', content: 'Ещё', artifacts: [], created_at: '' },
    ]);
    expect(turns.map((t) => [t.user, t.text])).toEqual([
      ['Привет', 'Здравствуйте!'],
      ['Ещё', ''],
    ]);
  });
});
