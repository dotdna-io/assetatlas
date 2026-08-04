# Chatbot Configuration

This tab selects and configures the large language model (LLM) that powers the AssetAtlas chatbot. Each supported provider has its own card; one provider is active at a time. If a banner says the chatbot is disabled, it must first be enabled in the server configuration (and the API restarted) — settings you save here take effect only once it's on.

## Choosing a provider

There is a card for **Anthropic**, **OpenAI**, **AWS Bedrock**, **OpenAI-compatible** (self-hosted vLLM, Ollama, LiteLLM), and **Microsoft Foundry**. The toggle in a card's header makes that provider the active one, taking over from whichever was active. You can only enable a provider whose card has no unsaved edits **and** has passed a connection test in the current session — so save the card and run **Test connection** first (editing or saving a card clears its last test result). The active provider's toggle can always be switched off, which turns the chatbot off entirely.

## Configuring a card

- **Model** — the model id to call. A grey placeholder shows a typical value for that provider.
- **API key** — shows a masked preview when a key is stored, or *Not configured*. Click the edit (pencil) icon to enter a new key; the eye icon reveals what you're typing. The stored key is never displayed and is left untouched unless you enter a new one.
- **OpenAI-compatible** also needs a **Base URL** (the full endpoint including `/v1`) and has a **Reasoning** toggle.
- **Microsoft Foundry** also needs a **Protocol** (*Anthropic Messages*, *OpenAI Chat Completions*, or *OpenAI Responses*) and a **Target URL** — the full URL copied from Foundry, including any `?api-version=…`.
- **Anthropic** has a **Prompt caching** toggle that caches the system prompt and tool spec for cheaper repeat calls.
- **AWS Bedrock** uses **Authentication** (*Access keys* or *Assume role*) plus an **AWS Region**. Access-key mode takes an Access Key ID and Secret Access Key; assume-role mode takes an IAM Role ARN, an optional External ID, and optional base credentials used to assume the role. Its **Model** is a dropdown — click **Refresh** to list the models available in the region; if AWS can't be reached, the dropdown falls back to a built-in list and says so.

Each card has its own buttons: **Cancel** discards that card's unsaved edits, **Test connection** calls the LLM with the card's current values, and **Save** persists them. Cancel and Save are only enabled when the card has unsaved changes.

## Testing

**Test connection** sends a probe request using whatever is currently in the card (including a freshly typed key, even before saving). The button stays disabled until the card's required fields are filled. On success the button turns green with *Connection successful*; on failure a dialog shows the provider's error detail. A passing test doesn't save the card — you still click **Save** — but it's what unlocks the enable toggle for an inactive provider.
