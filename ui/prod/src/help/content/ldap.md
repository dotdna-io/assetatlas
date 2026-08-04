# LDAP Authentication

Configure LDAP / Active Directory authentication. When enabled, login attempts are verified against the directory first; local password verification is used as a fallback unless that fallback is turned off.

## Configuring LDAP

Fill in the settings card and click **Save**. Save is per-card and only enabled once you change something; resetting a row returns it to its default. The master **Enabled** toggle plus a reachable **Server URL** are both required before LDAP is actually used for logins.

## Testing the connection

Use **Test connection** to dry-run the directory without creating or modifying any local account.

- The dialog asks for a **Username** and **Password** and performs a live bind + search using the **currently-saved** settings — save your changes first.
- On success it shows the resolved **User DN**, the **Mapped role** the user would get, and the **Attributes** read back (email, full name, groups).
- On failure it lists the errors, e.g. *LDAP is not enabled in settings*, *user not found in directory under the configured base DN/filter*, *user found but bind with the supplied password failed*, or a raw *LDAP error: …*.

## Key fields

- **Server URL** — `ldap://host:389` or `ldaps://host:636`. **Use SSL** and **Verify cert** control TLS; disabling SSL sends bind credentials in cleartext.
- **Bind DN / Bind password** — the service account used to search for users. The password is stored encrypted at rest.
- **Base DN** and **User search filter** — where and how users are located. The `{username}` placeholder in the filter is replaced with the name being signed in.
- **Attribute mail / full name / groups** — directory attributes mapped onto the AssetAtlas account (defaults `mail`, `displayName`, `memberOf`).
- **Group to role mapping** — JSON mapping a group DN (matched case-insensitively) to a role (`user`, `admin`, or `super_admin`). **Default role** applies when no mapped group matches.
- **Allow local fallback** — when on, a failed LDAP check falls through to local password verification. Turn it off to enforce LDAP-only logins — but keep it on until LDAP is proven working, since with it off an LDAP outage or misconfiguration leaves no local way back in.
- **Connect timeout** — how long to wait (seconds, default 10) when binding to the directory before giving up.
