// SPDX-License-Identifier: AGPL-3.0-or-later
import { memo, useMemo, useState } from 'react';

import type { ChatMessage } from '../../api/chat';
import type { ChartArtifact as ChartArtifactPayload } from './chartArtifactTypes';
import type { ToolCallInfo, ToolCallStatus } from './hooks/useSSEStream';
import { AssistantActions } from './AssistantActions';
import { AssistantMarkdown } from './AssistantMarkdown';
import { ChartArtifact } from './ChartArtifact';
import { artifactFromToolCall } from './chartArtifactTypes';
import { STATUS_SYMBOL, ToolActivity, buildToolLabel } from './ToolActivity';
import styles from './Message.module.css';

interface MessageProps {
  message: Partial<Pick<ChatMessage, 'id'>> &
    Pick<ChatMessage, 'role' | 'content' | 'tool_calls'>;
  streaming?: boolean;
  /** Live tool-call chips to append to the streaming assistant bubble. */
  liveToolCalls?: ToolCallInfo[];
  /** Regenerate-this-answer handler; MessageList only provides one when a
   *  user message precedes this assistant message. */
  onRegenerate?: () => void;
  /** Chart artifacts produced earlier in this turn's thinking bubbles,
   *  rolled onto the final answer so the user sees them next to the
   *  text they're reading (not on a collapsed intermediate). Computed
   *  by MessageList. */
  inheritedArtifacts?: ChartArtifactPayload[];
}

function parseHistoricalToolCalls(raw: unknown): ToolCallInfo[] {
  if (!Array.isArray(raw)) return [];
  const out: ToolCallInfo[] = [];
  for (const tc of raw) {
    if (!tc || typeof tc !== 'object' || !tc.id || !tc.name) continue;
    // Persisted message.tool_calls records the Anthropic request shape
    // (id + name + arguments), not the execution status — which lives
    // in the chat_tool_calls table. Assume "ok" for historical chips.
    out.push({
      id: String(tc.id),
      name: String(tc.name),
      status: 'ok',
      artifact: artifactFromToolCall(tc),
    });
  }
  return out;
}

/** A persisted tool call with its request arguments kept, for the expanded
 *  "Used <tools>" view. The execution status isn't on the message JSON (it
 *  lives in chat_tool_calls), so historical calls are assumed "ok" — same
 *  assumption as parseHistoricalToolCalls. */
interface ThinkingDetail {
  id: string;
  name: string;
  status: ToolCallStatus;
  args: Record<string, unknown> | null;
}

function parseThinkingDetails(raw: unknown): ThinkingDetail[] {
  if (!Array.isArray(raw)) return [];
  const out: ThinkingDetail[] = [];
  for (const tc of raw) {
    if (!tc || typeof tc !== 'object') continue;
    const t = tc as Record<string, unknown>;
    if (!t.id || !t.name) continue;
    const args = t.arguments;
    out.push({
      id: String(t.id),
      name: String(t.name),
      status: 'ok',
      args:
        args && typeof args === 'object' && !Array.isArray(args)
          ? (args as Record<string, unknown>)
          : null,
    });
  }
  return out;
}

function MessageInner({ message, streaming, liveToolCalls,
                       onRegenerate, inheritedArtifacts }: MessageProps) {
  const role = message.role;
  const historicalCalls = useMemo(
    () => parseHistoricalToolCalls(message.tool_calls),
    [message.tool_calls],
  );
  const thinkingDetails = useMemo(
    () => parseThinkingDetails(message.tool_calls),
    [message.tool_calls],
  );

  if (role === 'system') {
    return (
      <div className={`${styles.row} ${styles.system}`}>
        <div className={`${styles.bubble} ${styles.systemBubble}`}>
          {message.content || ''}
        </div>
      </div>
    );
  }

  const isUser = role === 'user';
  const bubbleClass = isUser ? styles.userBubble : styles.assistantBubble;
  const chips = streaming && liveToolCalls ? liveToolCalls : historicalCalls;

  // Intermediate "thinking" step: assistant produced some prose and then
  // called tools. These are collapsed by default in history; the final
  // answer (no tool_calls) stays expanded. Streaming bubble is also
  // always expanded so the user can watch the bot work.
  const isThinking =
    role === 'assistant' && !streaming && Array.isArray(message.tool_calls)
    && message.tool_calls.length > 0;

  // AssistantActions (Open-in-Explorer chip) belongs on the final answer
  // only. Intermediate thinking bubbles would stack identical chips up the
  // view with no actionable difference.
  const showActions = !isUser && !streaming && !!message.id &&
                      role === 'assistant' && !isThinking;

  // Charts belong to the final answer only. Intermediate thinking
  // bubbles are collapsed and can repeat across a turn — rendering their
  // donuts would stack identical charts on each thinking step.
  const artifacts = useMemo(() => collectArtifacts(chips, inheritedArtifacts),
                            [chips, inheritedArtifacts]);
  const showArtifacts = role === 'assistant' && !isThinking && artifacts.length > 0;

  return (
    <div className={`${styles.row} ${styles[role]}`}>
      <div className={`${styles.bubble} ${bubbleClass}`}>
        {isThinking
          ? <CollapsedThinking content={message.content} details={thinkingDetails} />
          : (
            <>
              {role === 'assistant' && message.content
                ? <AssistantMarkdown content={message.content} />
                : (message.content || '')}
              <ToolActivity calls={chips} />
              {showArtifacts && artifacts.map((a, i) => (
                <ChartArtifact
                  key={`${a.meta.source_tool}-${a.title}-${i}`}
                  artifact={a}
                />
              ))}
            </>
          )}
        {showActions && (
          <AssistantActions
            messageId={message.id as string}
            toolCalls={message.tool_calls}
            onRegenerate={onRegenerate}
          />
        )}
      </div>
    </div>
  );
}


// Streaming bubbles get artifacts from their live tool calls; persisted
// final answers get them from MessageList's turn walk (``inheritedArtifacts``).
// Both paths converge on the same ``ChartArtifact`` list here.
function collectArtifacts(
  chips: ToolCallInfo[],
  inherited: ChartArtifactPayload[] | undefined,
): ChartArtifactPayload[] {
  const fromChips = chips
    .map((c) => c.artifact)
    .filter((a): a is ChartArtifactPayload => !!a);
  if (fromChips.length > 0) return fromChips;
  return inherited ?? [];
}

function CollapsedThinking({
  content,
  details,
}: {
  content: string | null | undefined;
  details: ThinkingDetail[];
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className={styles.thinkingToggle}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={styles.caret}>{open ? '▾' : '▸'}</span>
        <span>Used {buildToolLabel(details)}</span>
      </button>
      {open && (
        <div className={styles.thinkingBody}>
          {content ? <AssistantMarkdown content={content} /> : ''}
          <ToolCallDetails details={details} />
        </div>
      )}
    </>
  );
}

/** Expanded view of a turn's tool calls: each tool's name, status, and the
 *  arguments it was invoked with. The raw tool result isn't on the client
 *  (only chart artifacts, which render on the final answer), so this shows
 *  the call itself, not a result dump. */
function ToolCallDetails({ details }: { details: ThinkingDetail[] }) {
  if (details.length === 0) return null;
  return (
    <div className={styles.toolDetails}>
      {details.map((d, i) => (
        <div key={d.id || i} className={styles.toolDetail}>
          <span className={styles.toolDetailStatus} aria-hidden="true">
            {STATUS_SYMBOL[d.status]}
          </span>
          <span className={styles.toolDetailName}>{d.name}</span>
          {d.args && Object.keys(d.args).length > 0 && (
            <code className={styles.toolDetailArgs}>
              {JSON.stringify(d.args)}
            </code>
          )}
        </div>
      ))}
    </div>
  );
}

export const Message = memo(MessageInner);
