// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

// Stub the vanilla-JS-wrapping chart so Message can render ChartArtifact
// without needing DarkModeProvider or the window.DotDNA globals. Must be
// declared BEFORE the import of Message (vi.mock is hoisted regardless).
vi.mock('@/components/charts/DotDNAChart', () => ({
  DotDNAChart: () =>
    React.createElement('div', { 'data-testid': 'dotdna-chart-stub' }),
}));

import { ChatBubble } from '../ChatBubble';
import { ChatProvider, useChatUI } from '../ChatProvider';
import { ChatHeader } from '../ChatHeader';
import { ChatInput } from '../ChatInput';
import { Message } from '../Message';
import { MessageList } from '../MessageList';
import { StreamingIndicator } from '../StreamingIndicator';
import { ToolActivity } from '../ToolActivity';
import { makeDonutArtifact } from './fixtures';

const DONUT = makeDonutArtifact();

function withProvider(ui: React.ReactNode) {
  return render(<ChatProvider>{ui}</ChatProvider>);
}

describe('ChatBubble', () => {
  it('toggles open state on click', () => {
    function Probe() {
      const { open } = useChatUI();
      return <div data-testid="state">{open ? 'open' : 'closed'}</div>;
    }
    withProvider(
      <>
        <ChatBubble />
        <Probe />
      </>,
    );
    expect(screen.getByTestId('state').textContent).toBe('closed');
    fireEvent.click(screen.getByRole('button', { name: /open chat/i }));
    expect(screen.getByTestId('state').textContent).toBe('open');
  });
});

describe('ChatHeader', () => {
  it('renders title and fires actions', () => {
    const onNew = vi.fn();
    render(<ChatHeader title="Hello" onNewConversation={onNew} />);
    expect(screen.getByText('Hello')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('New conversation'));
    expect(onNew).toHaveBeenCalled();
    // No close button — the floating chat bubble toggles the panel.
    expect(screen.queryByLabelText('Close chat')).not.toBeInTheDocument();
  });

});

describe('ChatInput', () => {
  it('sends on Enter, ignores Shift+Enter', () => {
    const onSend = vi.fn();
    render(<ChatInput onSend={onSend} />);
    const ta = screen.getByRole('textbox', {
      name: /chat message/i,
    }) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'hello' } });
    fireEvent.keyDown(ta, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('hello');

    onSend.mockClear();
    fireEvent.change(ta, { target: { value: 'not yet' } });
    fireEvent.keyDown(ta, { key: 'Enter', shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('send button is disabled on empty input', () => {
    render(<ChatInput onSend={vi.fn()} />);
    const send = screen.getByRole('button', { name: /send/i });
    expect(send).toBeDisabled();
  });

  it('shows Stop button while streaming', () => {
    const onCancel = vi.fn();
    render(<ChatInput onSend={vi.fn()} onCancel={onCancel} streaming />);
    const stop = screen.getByRole('button', { name: /stop/i });
    fireEvent.click(stop);
    expect(onCancel).toHaveBeenCalled();
  });

  it('restores the last sent prompt to the input on Stop (keep & edit)', () => {
    const onSend = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <ChatInput onSend={onSend} onCancel={onCancel} />,
    );
    const ta = screen.getByRole('textbox', {
      name: /chat message/i,
    }) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'how many laptops?' } });
    fireEvent.keyDown(ta, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('how many laptops?');
    // Cleared on send…
    expect(ta.value).toBe('');
    // …then the turn starts streaming and the user hits Stop to revise.
    rerender(<ChatInput onSend={onSend} onCancel={onCancel} streaming />);
    fireEvent.click(screen.getByRole('button', { name: /stop/i }));
    expect(onCancel).toHaveBeenCalled();
    // The prompt is back in the box, editable.
    expect(ta.value).toBe('how many laptops?');
  });

  it('does not clobber a draft typed during streaming when Stop is hit', () => {
    const onSend = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <ChatInput onSend={onSend} onCancel={onCancel} />,
    );
    const ta = screen.getByRole('textbox', {
      name: /chat message/i,
    }) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'first question' } });
    fireEvent.keyDown(ta, { key: 'Enter' });
    // Turn streams; the user starts a new draft in the box, then hits Stop.
    rerender(<ChatInput onSend={onSend} onCancel={onCancel} streaming />);
    fireEvent.change(ta, { target: { value: 'a different draft' } });
    fireEvent.click(screen.getByRole('button', { name: /stop/i }));
    expect(onCancel).toHaveBeenCalled();
    // Draft preserved — the last-sent text does NOT overwrite it.
    expect(ta.value).toBe('a different draft');
  });
});

describe('ToolActivity', () => {
  it('renders nothing when there are no calls', () => {
    const { container } = render(<ToolActivity calls={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('shows "Using …" with a spinner while any call is running', () => {
    const { container } = render(
      <ToolActivity
        calls={[
          { id: '1', name: 'list_assets', status: 'ok' },
          { id: '2', name: 'get_asset', status: 'running' },
        ]}
      />,
    );
    const toggle = screen.getByRole('button', {
      name: /Using list_assets, get_asset/,
    });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(container.querySelector('[class*="spinner"]')).not.toBeNull();
  });

  it('shows "Used …" without a spinner once all calls settled', () => {
    const { container } = render(
      <ToolActivity
        calls={[
          { id: '1', name: 'list_assets', status: 'ok' },
          { id: '2', name: 'get_asset', status: 'error' },
        ]}
      />,
    );
    expect(
      screen.getByRole('button', { name: /Used list_assets, get_asset/ }),
    ).toBeInTheDocument();
    expect(container.querySelector('[class*="spinner"]')).toBeNull();
  });

  it('expands to per-call rows with status symbols on click', () => {
    render(
      <ToolActivity
        calls={[
          { id: '1', name: 'list_assets', status: 'ok' },
          { id: '2', name: 'list_assets', status: 'ok' },
          { id: '3', name: 'get_asset', status: 'running' },
        ]}
      />,
    );
    // Collapsed: rows are not rendered at all (conditional rendering —
    // jsdom applies no CSS, so visibility cannot be asserted).
    expect(screen.queryByText('✓')).not.toBeInTheDocument();
    expect(screen.queryByText('…')).not.toBeInTheDocument();
    expect(screen.queryByText('get_asset')).not.toBeInTheDocument();
    const toggle = screen.getByRole('button', { name: /Using/ });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getAllByText('✓')).toHaveLength(2);
    expect(screen.getByText('…')).toBeInTheDocument();
    expect(screen.getAllByText('list_assets')).toHaveLength(2);
    expect(screen.getByText('get_asset')).toBeInTheDocument();
    // Toggles shut again.
    fireEvent.click(toggle);
    expect(screen.queryByText('✓')).not.toBeInTheDocument();
  });

  it('collapses duplicate tools in the label (buildToolLabel ×2)', () => {
    render(
      <ToolActivity
        calls={[
          { id: '1', name: 'list_assets', status: 'ok' },
          { id: '2', name: 'list_assets', status: 'ok' },
          { id: '3', name: 'get_asset', status: 'ok' },
        ]}
      />,
    );
    expect(
      screen.getByRole('button', { name: /Used list_assets ×2, get_asset/ }),
    ).toBeInTheDocument();
  });
});

describe('Message', () => {
  it('renders user bubble', () => {
    render(
      <Message message={{ role: 'user', content: 'hi', tool_calls: null }} />,
    );
    expect(screen.getByText('hi')).toBeInTheDocument();
  });

  it('collapses intermediate assistant messages (with tool_calls) by default', () => {
    render(
      <Message
        message={{
          id: 'm1',
          role: 'assistant',
          content: 'Let me check…',
          tool_calls: [
            { id: 't1', name: 'aggregate_assets', arguments: {} },
          ],
        }}
      />,
    );
    expect(screen.queryByText('Let me check…')).not.toBeInTheDocument();
    // Label names the tool(s) used, replacing the old "Thinking ·" wording.
    expect(screen.getByText(/Used aggregate_assets/)).toBeInTheDocument();
  });

  it('shows distinct tool names in the Thinking label', () => {
    render(
      <Message
        message={{
          id: 'm2',
          role: 'assistant',
          content: 'working',
          tool_calls: [
            { id: 'a', name: 'list_assets', arguments: {} },
            { id: 'b', name: 'list_assets', arguments: {} },
            { id: 'c', name: 'get_asset', arguments: {} },
          ],
        }}
      />,
    );
    // Summarised: repeat-count for duplicates, comma for distinct.
    expect(
      screen.getByText(/Used list_assets ×2, get_asset/),
    ).toBeInTheDocument();
  });

  it('suppresses Open in Explorer on thinking bubbles (chip only on finals)', () => {
    render(
      <Message
        message={{
          id: 'm3',
          role: 'assistant',
          content: 'checking',
          tool_calls: [
            { id: 't1', name: 'list_assets', arguments: {} },
          ],
        }}
      />,
    );
    expect(screen.queryByText(/open in explorer/i)).not.toBeInTheDocument();
  });

  it('expands a thinking bubble when the toggle is clicked', () => {
    render(
      <Message
        message={{
          id: 'm1',
          role: 'assistant',
          content: 'Let me check…',
          tool_calls: [
            { id: 't1', name: 'aggregate_assets', arguments: {} },
          ],
        }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Used/ }));
    expect(screen.getByText('Let me check…')).toBeInTheDocument();
    expect(screen.getByText('aggregate_assets')).toBeInTheDocument();
  });

  it('shows tool arguments in the expanded "Used" bubble', () => {
    render(
      <Message
        message={{
          id: 'm-args',
          role: 'assistant',
          content: 'checking',
          tool_calls: [
            { id: 't1', name: 'aggregate_assets',
              arguments: { group_by: 'os' } },
          ],
        }}
      />,
    );
    // Collapsed: args hidden until expanded.
    expect(screen.queryByText(/group_by/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Used/ }));
    // Expanded: the call's arguments are visible beside the tool name.
    expect(screen.getByText(/group_by/)).toBeInTheDocument();
    expect(screen.getByText(/os/)).toBeInTheDocument();
  });

  it('renders final assistant answer (no tool_calls) fully expanded', () => {
    render(
      <Message
        message={{
          id: 'm2',
          role: 'assistant',
          content: 'Found 5 assets.',
          tool_calls: null,
        }}
      />,
    );
    expect(screen.getByText('Found 5 assets.')).toBeInTheDocument();
    expect(screen.queryByText(/Used/)).not.toBeInTheDocument();
  });

  it('uses live tool calls when streaming', () => {
    render(
      <Message
        streaming
        liveToolCalls={[{ id: 'x', name: 'live_tool', status: 'running' }]}
        message={{
          role: 'assistant',
          content: '',
          tool_calls: [{ id: 'y', name: 'other', arguments: {} }],
        }}
      />,
    );
    // The unified chip summarises the live call; the persisted historical
    // call never reaches the streaming bubble.
    const toggle = screen.getByRole('button', { name: /Using live_tool/ });
    expect(screen.queryByText(/other/)).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByText('live_tool')).toBeInTheDocument();
    expect(screen.queryByText('other')).not.toBeInTheDocument();
  });

  it('renders a chart artifact inline for a final assistant answer', () => {
    // A final answer has tool_calls=null. MessageList inherits the chart
    // forward from the preceding thinking bubble's tool_calls.
    render(
      <Message
        message={{
          id: 'm-final',
          role: 'assistant',
          content: 'Here is the breakdown.',
          tool_calls: null,
        }}
        inheritedArtifacts={[DONUT]}
      />,
    );
    expect(screen.getByText('Here is the breakdown.')).toBeInTheDocument();
    expect(screen.getByTestId('dotdna-chart-stub')).toBeInTheDocument();
    expect(screen.getByText('Assets by Category')).toBeInTheDocument();
  });

  it('renders a chart during streaming from the live tool call', () => {
    render(
      <Message
        streaming
        liveToolCalls={[
          { id: 'tc1', name: 'aggregate_assets', status: 'ok',
            artifact: DONUT },
        ]}
        message={{
          role: 'assistant',
          content: 'Streaming answer…',
          tool_calls: null,
        }}
      />,
    );
    expect(screen.getByTestId('dotdna-chart-stub')).toBeInTheDocument();
  });

  it('does NOT render a chart in a thinking bubble (collapsed intermediate)', () => {
    // A thinking bubble (tool_calls present, not streaming) is collapsed
    // by default; rendering the chart inside its closed body would hide
    // it and stacking it on expansion would duplicate the final answer's.
    render(
      <Message
        message={{
          id: 'm-mid',
          role: 'assistant',
          content: 'let me check…',
          tool_calls: [
            { id: 't1', name: 'aggregate_assets', arguments: {},
              artifact: DONUT },
          ],
        }}
      />,
    );
    expect(screen.queryByTestId('dotdna-chart-stub')).not.toBeInTheDocument();
  });
});

describe('MessageList', () => {
  it('shows error bar when error is set', () => {
    render(
      <MessageList
        messages={[]}
        streaming={false}
        streamingText=""
        streamingToolCalls={[]}
        error="rate_limited"
      />,
    );
    expect(screen.getByText('rate_limited')).toBeInTheDocument();
  });

  it('renders streaming assistant bubble with delta text', () => {
    render(
      <MessageList
        messages={[]}
        streaming
        streamingText="partial "
        streamingToolCalls={[]}
        error={null}
      />,
    );
    expect(screen.getByText(/partial/)).toBeInTheDocument();
  });

  it('wires Regenerate on a final assistant message back to the preceding user message text', () => {
    const onRegenerate = vi.fn();
    const base = {
      seq: 0, tool_calls: null, input_tokens: null,
      output_tokens: null, created_at: 'x',
    };
    render(
      <MessageList
        messages={[
          { id: 'u1', role: 'user', content: 'how many workstations?',
            ...base, seq: 1 } as any,
          { id: 'a1', role: 'assistant', content: '5,728.',
            ...base, seq: 2 } as any,
        ]}
        streaming={false}
        streamingText=""
        streamingToolCalls={[]}
        error={null}
        onRegenerate={onRegenerate}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /regenerate/i }));
    expect(onRegenerate).toHaveBeenCalledWith('how many workstations?');
  });

  it('forwards a preceding thinking bubble\'s chart onto the final answer', () => {
    // Backend persists the artifact on the tool_use block of the
    // *thinking* message; the user sees it on the *final* answer. This
    // test locks in the walk MessageList does to move it forward.
    const base = {
      seq: 0, input_tokens: null,
      output_tokens: null, created_at: 'x',
    };
    render(
      <MessageList
        messages={[
          { id: 'u1', role: 'user', content: 'breakdown?',
            tool_calls: null, ...base, seq: 1 } as any,
          { id: 'a1', role: 'assistant', content: 'Let me check…',
            tool_calls: [
              { id: 't1', name: 'aggregate_assets', input: {},
                artifact: DONUT },
            ],
            ...base, seq: 2 } as any,
          { id: 'a2', role: 'assistant', content: 'Here it is.',
            tool_calls: null, ...base, seq: 3 } as any,
        ]}
        streaming={false}
        streamingText=""
        streamingToolCalls={[]}
        error={null}
      />,
    );
    // Final answer text and chart stub both present; thinking bubble
    // stays collapsed so its instance of the chart is NOT mounted.
    expect(screen.getByText('Here it is.')).toBeInTheDocument();
    const stubs = screen.getAllByTestId('dotdna-chart-stub');
    expect(stubs).toHaveLength(1);
  });
});

describe('StreamingIndicator', () => {
  it('renders dots when no text and no tool running', () => {
    render(<StreamingIndicator hasText={false} runningTool={null} />);
    expect(screen.getByText(/thinking/i)).toBeInTheDocument();
  });

  it('renders tool name when a tool is running', () => {
    render(
      <StreamingIndicator
        hasText={false}
        runningTool={{ id: 't', name: 'list_assets', status: 'running' }}
      />,
    );
    expect(screen.getByText(/list_assets/)).toBeInTheDocument();
    expect(screen.getByText(/looking up/i)).toBeInTheDocument();
  });

  it('renders nothing when text has arrived and no tool running', () => {
    const { container } = render(
      <StreamingIndicator hasText runningTool={null} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
