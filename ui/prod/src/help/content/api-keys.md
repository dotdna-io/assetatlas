# API Keys

API keys let external systems reach AssetAtlas without a user login. This tab lists every key and is where you create, revoke, and delete them.

A key has two uses, and they take **different headers**:

- **Calling the AssetAtlas API** — send the key as `X-API-Key`.
- **Connecting an AI assistant to the MCP server** — send it as `Authorization: Bearer aa_…`. See *MCP Server*.

Managing keys is admin-only, and a key acts with the role of the admin who created it — there's no per-key permission scope, so treat it as that account's credential.

## Creating a key

- Click **Create API Key**.
- Enter a **Name** (required) — a label identifying who or what uses the key.
- Set **Expiration**: pick a preset (**30 days**, **90 days**, **180 days**, **1 year**; 90 days is the default) or click **Custom** and choose a date. A custom date must be in the future (tomorrow at the earliest).
- Click **Create Key**.

The key is then shown once in a confirmation dialog. **Copy** it and store it securely — it is never displayed again. You must tick **I have copied and securely stored this key** before the dialog will close.

## Managing keys

The table shows each key's **Name**, **Key Prefix** (first characters only — the full key is never re-shown), **Created By**, **Expires At**, **Last Used**, and **Status** (Active, Expired, or Revoked).

- **Revoke** — sets the key inactive. Available only for keys that are still active and not expired. Any system using it loses access immediately. This cannot be undone, but the row remains for audit.
- **Delete** — permanently removes the key and its history. Available for any key. This cannot be undone.

Both actions ask for confirmation first.

## Key fields

- **Name** — 1–100 characters, required. Used only as a human label; it has no effect on access.
- **Status** — **Revoked** if the key was deactivated, **Expired** once past its expiration (even if never revoked), otherwise **Active**.
- **Expires At** — fixed at creation from the preset or custom date. It cannot be edited; create a new key to extend.
- **Last Used** — when the key was last used for a successful request, or *Never* if it has not been used. It covers both uses: an API call and an MCP request each advance it.

## Gotchas

- **The two headers are not interchangeable.** A key sent as `Authorization: Bearer` to the AssetAtlas API is rejected as a missing key, and a key sent as `X-API-Key` to the MCP server is rejected as missing authentication. Both come back as a plain 401 that names neither header, so a client configured with the wrong one looks like a bad key rather than a bad header.
- **The per-minute rate limit is looser than it reads, and does not cover MCP at all.** The AssetAtlas API runs four worker processes that each count a key's requests separately, so a key can pass the configured limit several times over before any one worker refuses it. The MCP server is a separate process with no counter at all, so nothing throttles a key used there — see *MCP Server*.
