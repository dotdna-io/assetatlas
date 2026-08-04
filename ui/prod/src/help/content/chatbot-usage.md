# Chatbot Usage

A read-only overview of chatbot activity over a chosen time window — totals, the busiest users, token spend per model, and per-tool call statistics.

## Reading the page

Pick the window at the top: **24h**, **7d**, **30d**, or **90d** (7d by default). Every figure on the page reflects only that window.

- **Stats strip** — **Messages**, **Sessions**, **Active users**, **Input tokens**, and **Output tokens** for the window.
- **Top users** — the users who sent the most messages, with their message counts.
- **Tokens by provider** — one row per provider/model pair, with input tokens, output tokens, and message count. This is where token spend per model is visible.
- **Tool calls** — one row per tool the chatbot invoked: total **Calls**, **Errors**, **Timeouts**, **Denied**, the error rate, and average duration in milliseconds. The error rate counts errors and timeouts, not denied calls.

Each table shows its own message ("No activity in this window.", "No provider activity.", "No tool calls in this window.") when there is nothing to display.
