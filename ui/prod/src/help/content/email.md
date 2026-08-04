# Email & SMTP

Configure the SMTP relay AssetAtlas uses for outbound mail (account invitations, password resets, and alert notifications). The tab has a master **Enable outbound email** toggle and two cards: **SMTP transport** and **Identity & links**.

## Configuring SMTP

Fill in both cards and click **Save**.

- Turn on **Enable outbound email**. While it's off, every send is short-circuited — no mail leaves the system. The one exception is **Send test email**, which works either way, so you can confirm the relay before you switch real mail on.
- **SMTP transport** — set **SMTP host**, **SMTP port**, **Security**, and (if your relay requires auth) **Username** / **Password**.
- **Identity & links** — set the **From address** and **From display name** that recipients see; **Reply-To** is optional.
- Click **Save**.

## Sending a test email

Use **Send test email** to send a fixed test message and confirm the relay works.

- The button is enabled once the **saved** configuration has an SMTP host and a From address. Save your changes first — the unsaved form is not used.
- **Enable outbound email** does not have to be on. Test the relay first, then turn outbound mail on once you know it works.
- The dialog asks for a **Recipient**, then sends using the currently-saved settings.
- On success it confirms the email was sent. On failure the dialog shows the error the relay returned — check the host, port, security mode and credentials against what your relay expects.

## Key fields

- **SMTP port** — default 587. Use 587 with **starttls**, 465 with **tls**, 25 with **none**.
- **Security** — `none` (plain), `starttls` (connect plain, then upgrade), or `tls` (implicit TLS from connect). This choice determines how AssetAtlas opens the connection — it must match what the port expects.
- **Username / Password** — leave **Username** blank for an unauthenticated relay; auth is only attempted when a username is set. The password is stored encrypted and never returned: a saved password shows a `•••••••• (saved — leave blank to keep)` placeholder.
- **Reply-To** — optional; blank means no Reply-To header is added.
