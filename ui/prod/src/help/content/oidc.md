# OIDC Single Sign-On

Configure OpenID Connect (OIDC) Single Sign-On. When enabled, users can sign in through your identity provider (for example Entra ID, Okta, or Keycloak).

## Configuring OIDC

Fill in the settings card and click **Save** — Save is per-card and enabled only when something changed; a row's **Reset** restores its default. SSO is active only once the master **Enabled** toggle is on and **Discovery URL**, **Client ID**, **Client secret**, and **Redirect URI** are all set.

## Testing discovery

**Test discovery** opens a dialog that immediately fetches the OIDC discovery document from the configured issuer, using the **currently-saved** settings.

- On success it shows the **Issuer**, **Authorization endpoint**, **Token endpoint**, and **JWKS URI** read from the document.
- On failure it lists the errors, e.g. *OIDC is not enabled in settings* or *OIDC discovery error: …*.

This only validates that the discovery URL is reachable and well-formed; it does not perform a sign-in.

## Key fields

- **Provider name** — the label shown on the login button.
- **Discovery URL** — the provider's `.well-known/openid-configuration` URL.
- **Client ID / Client secret** — issued by the IdP; the secret is stored encrypted at rest.
- **Redirect URI** — the public callback URL; it must match what is registered in the IdP exactly.
- **Scopes** and **Claim mail / name / groups** — what is requested and which ID-token claims map to email, display name, and groups.
- **Group to role mapping** — JSON mapping group claim values (matched case-insensitively) to a role (`user`, `admin`, `super_admin`). **Default role** applies when nothing matches.
- **Allow local fallback** — keep local password login available. Disable to require SSO for everyone — but keep it on until SSO is proven working, or a provider outage leaves no way back in.
- **Auto-create users** — provision a local record on first SSO sign-in. **Allowed email domains** (comma-separated) restricts which domains may auto-provision, and is consulted only when auto-create is on. With auto-create off, an SSO user must already have a local account (created by an admin) or they can't sign in.
- **Advanced** — the card also carries a **Frontend complete URL** (where the browser lands after sign-in, default `/oidc-complete`), an **HTTP timeout** (seconds, default 10) for discovery and token calls, and an optional **Post-logout redirect URI**.
