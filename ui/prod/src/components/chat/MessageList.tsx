// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useMemo, useRef } from 'react';

import type { ChatMessage } from '../../api/chat';
import type { ChartArtifact } from './chartArtifactTypes';
import { artifactFromToolCall } from './chartArtifactTypes';
import type { ToolCallInfo } from './hooks/useSSEStream';
import { Message } from './Message';
import { StreamingIndicator } from './StreamingIndicator';
import styles from './MessageList.module.css';


function isThinkingMessage(m: ChatMessage): boolean {
  return (
    m.role === 'assistant'
    && Array.isArray(m.tool_calls)
    && m.tool_calls.length > 0
  );
}

interface MessageListProps {
  messages: ChatMessage[];
  streaming: boolean;
  streamingText: string;
  streamingToolCalls: ToolCallInfo[];
  error: string | null;
  /** Called with a user message's text when the reader clicks "Regenerate"
   *  on the following assistant answer. MessageList owns the "which user
   *  message precedes this assistant one" mapping. */
  onRegenerate?: (userText: string) => void;
}

export function MessageList({
  messages,
  streaming,
  streamingText,
  streamingToolCalls,
  error,
  onRegenerate,
}: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // `auto` (instant) keeps up with high-frequency delta events without the
    // layout thrash that smooth scrolling causes at 50+ deltas/sec.
    endRef.current?.scrollIntoView?.({ behavior: 'auto', block: 'end' });
  }, [messages.length, streamingText, streamingToolCalls.length, streaming]);

  const runningTool =
    streamingToolCalls.find((c) => c.status === 'running') ?? null;
  const showIndicator = streaming && !streamingText;

  // For each turn's final assistant message, precompute (a) a stable
  // Regenerate callback keyed on the preceding user text, and (b) the
  // chart artifacts produced by thinking bubbles inside that turn. Both
  // share the same walk: forward through messages; on hitting a final
  // answer (no tool_calls), scan backward until the turn-opening user
  // message. Without memoizing these, every render allocated a fresh
  // closure per final message — blowing React.memo(Message) and
  // triggering a full re-render of history on each streaming delta.
  const { regenerateByMessageId, artifactsByMessageId } = useMemo(() => {
    const regen = new Map<string, () => void>();
    const artifacts = new Map<string, ChartArtifact[]>();
    for (let i = 0; i < messages.length; i++) {
      const m = messages[i];
      if (m.role !== 'assistant' || isThinkingMessage(m)) continue;
      const acc: ChartArtifact[] = [];
      for (let j = i - 1; j >= 0; j--) {
        const prev = messages[j];
        if (prev.role === 'user') {
          if (onRegenerate && prev.content) {
            const text = prev.content as string;
            regen.set(m.id, () => onRegenerate(text));
          }
          break;
        }
        if (prev.role !== 'assistant' || !Array.isArray(prev.tool_calls)) continue;
        for (const tc of prev.tool_calls) {
          const a = artifactFromToolCall(tc);
          if (a) acc.push(a);
        }
      }
      // Reverse so charts display in the order they were produced.
      if (acc.length > 0) artifacts.set(m.id, acc.reverse());
    }
    return { regenerateByMessageId: regen, artifactsByMessageId: artifacts };
  }, [messages, onRegenerate]);

  return (
    <div className={styles.list} role="log" aria-live="polite">
      {error && <div className={styles.errorBar}>{error}</div>}

      {messages.map((m) => (
        <Message
          key={m.id}
          message={m}
          onRegenerate={regenerateByMessageId.get(m.id)}
          inheritedArtifacts={artifactsByMessageId.get(m.id)}
        />
      ))}

      {streaming && (streamingText || streamingToolCalls.length > 0) && (
        <Message
          message={{
            role: 'assistant',
            content: streamingText,
            tool_calls: null,
          }}
          streaming
          liveToolCalls={streamingToolCalls}
        />
      )}

      {showIndicator && (
        <StreamingIndicator hasText={false} runningTool={runningTool} />
      )}

      <div ref={endRef} />
    </div>
  );
}
