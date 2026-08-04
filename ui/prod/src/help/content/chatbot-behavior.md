# Chatbot Behavior

This tab tunes **how the chatbot selects its tools** — not which AI provider it uses (that's the *Chatbot Configuration* tab). It lives under **Settings → Chatbot → Behavior** and has two settings.

## Settings

- **Tool Routing Enabled** (default off) — when on, each chat turn advertises only a core tool set plus the tools relevant to the question (with a recovery path to find more) instead of exposing every tool. This cuts the input tokens spent per turn.
- **Tool Routing Top K** (default 5, range 1–20) — how many question-relevant tools to advertise per turn when tool routing is on. It has no effect while Tool Routing is off.

Edit either value and click **Save**; each row also has a reset-to-default control. Changes take effect on the next chat turn — no restart needed.
