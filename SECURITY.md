# Security Policy

## Reporting a Vulnerability

If you believe you have found a security vulnerability in AssetAtlas, please
report it privately by email to **security@dotdna.io**.

**Do not** open a public GitHub issue or pull request for a security
vulnerability — public disclosure before a fix is available puts users at risk.

Please include as much of the following as you can:

- A description of the vulnerability and its potential impact
- Steps to reproduce (proof-of-concept, affected endpoint or component)
- The affected version or commit
- Any suggested remediation

## Coordinated disclosure

We follow a coordinated-disclosure process:

1. We will acknowledge receipt of your report and begin investigating.
2. We will work on a fix and keep you informed of progress.
3. We ask that you give us a reasonable opportunity to release a fix before any
   public disclosure. We are happy to credit reporters who wish to be named.

## Supported Versions

AssetAtlas uses a `YYYY.WW.N` release scheme (year, ISO week number,
release-within-week). Security fixes target the **latest release** — we
recommend always running the most recent version.

## Security posture (by design)

Some behaviors are intentional, security-relevant design decisions:

- **`/api/docs` is intentionally unauthenticated.** The Swagger UI (`/api/docs`)
  and the OpenAPI schema it serves (`/api/apispec.json`) are served without an
  auth guard **by design**. They expose only the API *schema and UI*, never
  asset data — every data-bearing API route is individually protected by JWT
  (`@jwt_required`). Publishing the schema is a deliberate, low-risk choice.
