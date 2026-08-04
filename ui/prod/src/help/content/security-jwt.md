# Security & Password Policy

The Security tab (Settings → System → Security) holds session, public-URL, and password-policy settings. It is split into two cards: **Security** and **Password Policy**.

## Editing settings

Each card saves on its own. **Save** is disabled until you change a value, and saving is silent (no confirmation banner). Every row has a **Reset** that returns just that setting to its default. Numeric fields enforce the minimum and maximum shown on each row.

## Key fields

- **Session Timeout Minutes** (Security card) — inactivity timeout for a signed-in session, from 5 minutes to 1440 (24 hours). Default 60. (Per-request access-token lifetimes aren't configurable here.)
- **Base URL** (Security card) — the HTTPS URL where users reach AssetAtlas (e.g. `https://assetatlas.example.com`). It is used for email links, the Teams app, and the OIDC redirect URI. The field pre-fills with the URL the install is currently using; if you change it, re-check that the OIDC redirect URI still matches what's registered with your identity provider.
- **Password Expiration Days** (Password Policy card) — days a local-account password stays valid before the user must change it at next login. `0` means never expires; default 180. Has no effect on LDAP or OIDC users.
