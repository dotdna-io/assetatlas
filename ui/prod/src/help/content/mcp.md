# MCP Server

AssetAtlas runs a Model Context Protocol (MCP) server, so an AI assistant such as Claude can query your asset data directly instead of you exporting it. Clients authenticate with an AssetAtlas API key.

## Connecting a client

- Create a key under **Settings → Access & Security → API Keys** and copy it while the dialog is open — it is never shown again. See *API Keys*.
- Point the client at `https://<your-assetatlas-address>/mcp` — the same address and port you open AssetAtlas with in a browser, with `/mcp` on the end and **no trailing slash**.
- Send the key in the `Authorization` header as `Bearer aa_…`, not in the `X-API-Key` header the AssetAtlas API takes.

Most clients accept a configuration of this shape:

```json
{
  "mcpServers": {
    "assetatlas": {
      "type": "http",
      "url": "https://assetatlas.example.com:8443/mcp",
      "headers": {
        "Authorization": "Bearer aa_your_key_here"
      }
    }
  }
}
```

Nothing has to be switched on in AssetAtlas — the MCP server runs as part of every deployment. A sign-in token is accepted too, but it expires 60 minutes after sign-in, so a key is the only credential worth saving in a client configuration.

## What a connected client can read

Every tool the server exposes only reads — roughly three dozen of them, and not one creates, changes, or deletes anything. Between them they cover assets and their history, alerts and alert rules, tag and posture rules, pipeline runs and data quality, reports, enrichment datasets, integration diagnostics, the audit trail, and system settings. A client cannot reach any part of AssetAtlas the tools do not expose.

## Gotchas

- **An MCP-connected key is in practice an admin-level read credential.** A key carries no scope of its own and acts with the role of the admin who created it, and no tool checks that role, so whoever holds the key can read everything above — including the audit trail and system settings, where stored secrets are masked but nothing else is. Issue one key per client, keep the expiry short, and revoke it when the client goes away.
- **Nothing throttles MCP.** A key used against the AssetAtlas API is rate limited per minute; the MCP server is a separate process with no shared counter, so that limit does not apply here and nothing caps how fast a connected assistant queries. This was accepted deliberately, because every tool is read-only — but a busy client is unmetered load on the database.
- **The URL has no trailing slash.** `…/mcp/` is answered with a redirect that the server builds from its own address, which loses the `https`, so a client that follows it fails to connect instead of retrying. Use `…/mcp`.
- **A self-signed certificate stops the client before the key is ever sent.** An install that generated its own certificate presents one no client trusts, so the connection is refused at the TLS handshake. Either trust that certificate on the machine running the client, or put the instance behind a certificate from a public authority.
- **Revoking or expiring a key does not disconnect a running client.** The server re-checks the key on every request, so nothing needs restarting — but the client keeps its session and only fails on its next query, with an authentication error it may surface as a generic failure.
- **There is no list of connected clients.** The **Last Used** column on the **API Keys** tab advances on every MCP request, the same as for an API call, and that is the only signal a key is in use at all.
