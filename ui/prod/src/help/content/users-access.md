# Users & Access

Local user accounts, their roles, and account lifecycle live on the **Users** tab under Settings → Access. The Access group also holds **API Keys**, **LDAP**, **OIDC SSO**, and **Audit Log** as sibling tabs.

## Roles

Every user has exactly one role, picked from a fixed set:

- **User** — standard access.
- **Admin** — administrative access.
- **Super Admin** — highest privilege.

The role is set when creating a user and can be changed by editing them. The same three values are the options in the **Role** filter.

## Creating and editing users

- Click **Create User**. **Email** is always required; **Username** is required, cannot contain spaces, and cannot be changed after creation.
- **Send onboarding invite** is on by default — the user receives a link and sets their own password. After creation a dialog shows the invite URL, whether the email was sent, and when the invite expires.
- Turn the invite toggle off to set an initial **Password** yourself; it must satisfy every item in the strength meter.
- **Edit** changes email, full name, and role only. Username is locked and active/inactive state is not editable here — use the enable/disable action instead.

## Managing accounts

Open a user's actions menu (the ⋯ button) for the lifecycle actions:

- **Reset Password** — emails the user a reset link.
- **Force password change at next login** — current password keeps working until they next sign in.
- **Reset MFA** — their current authenticator stops working; they re-enroll on next login.
- **Send Invite** — only shown while an onboarding invite is still pending; reissues the link (invite links expire, so use this if onboarding wasn't completed in time).
- **Disable / Enable User** — a disabled user is logged out immediately and cannot sign in until re-enabled.
- **Delete User** — permanent; revokes all active sessions and cannot be undone.

**Reset Password**, **Force password change**, and **Reset MFA** apply to local accounts only — they're hidden for LDAP/SSO users (marked in the **Auth Type** column), whose credentials and MFA live in the directory or IdP.

Filter the list by search text (username or email), role, and status; the list is paginated (10 per page). The tab header also shows summary stat cards alongside the **Create User** button.
