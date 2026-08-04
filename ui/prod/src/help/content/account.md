# Account

**Account** is your own profile — your display name, password, and two-factor authentication. Reach it from the account menu; every signed-in user has one, no admin rights involved. It has three sections.

## Profile

Your **Username** and **Email** are shown but can't be changed here (an admin changes email on the Users tab). Edit your **Display name** and click **Save changes**.

## Password

Shown for local accounts only — LDAP and SSO users change their password with their identity provider. Enter your **Current password** and a **New password** (at least 8 characters; a strength meter guides you) twice, then **Change password**. Your existing sessions stay valid after a change; sign other devices out yourself if you need to. If your password has expired, you're brought straight to this section and must change it before you can continue.

## Two-factor authentication

- **Local accounts** — click **Add an authenticator app**, scan the QR code (or use the manual-entry secret) in an authenticator app, enter the 6-digit code, and **Verify and enable**. Enrolled methods can be renamed or removed, and you can add more than one — but you can't remove your last one, so you always keep a way to sign in.
- **LDAP / SSO accounts** — MFA is handled by your identity provider; there's nothing to enrol here.
