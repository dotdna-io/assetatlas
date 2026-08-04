# Chatbot Teams

This tab connects the AssetAtlas chatbot to Microsoft Teams through an Azure Bot, so users can chat with it from inside Teams. It does not control which AssetAtlas users may use the chatbot.

## Before you start

Two things have to be in place first. The tab will happily let you fill in every field without an Azure Bot behind them — but it derives its messaging endpoint from the public URL, so with no public URL configured at all the tab never finishes loading and shows only its spinner.

- **An Azure Bot resource** in your Microsoft 365 tenant, with its Application (client) ID, a client secret, and its Tenant ID to hand. AssetAtlas configures only its own half of the connection — it creates nothing in Azure.
- **A public HTTPS address that reaches this AssetAtlas instance.** Microsoft's Bot Framework runs in Azure and delivers every Teams message to AssetAtlas, so the address it calls has to resolve in public DNS, be HTTPS, and present a certificate from a public certificate authority — self-signed certificates are rejected. **AssetAtlas ships no tunnel or relay component**, so exposing the instance is your IT team's job; a DMZ reverse proxy, Cloudflare Tunnel, or Azure Relay are all common ways of doing it. Set the resulting address as **Base URL** under **Settings → System → Security** — see *Security & Password Policy*.

An installation with no route in from the internet — air-gapped, or reachable only on your internal network — cannot use the Teams bot at all, whatever is configured on this tab. The in-app chatbot is unaffected.

## Configure the tab

The fields stay disabled until **Enable Microsoft Teams chatbot** is ticked. Each field is saved on its own — the IDs and endpoint save when you click away from them; the client secret saves only when you click its **Save** button.

- Tick **Enable Microsoft Teams chatbot**.
- Under **Azure Bot credentials**, enter the **Application (client) ID** and **Tenant ID** from your Azure Bot (both must be valid UUIDs).
- For **Client secret**, click the edit (pencil) icon, paste the Azure Bot client secret, and click **Save**. A stored secret is shown only as a masked placeholder, never the value.
- Under **Messaging endpoint**, copy the suggested URL (use the **Copy** button) into Azure Portal → your Azure Bot → Configuration → Messaging endpoint. Leaving the field empty lets AssetAtlas compute the endpoint from the **Base URL**; entering a value overrides that.
- In Azure Portal, open your bot's **Channels** and add **Microsoft Teams**. Nothing in AssetAtlas checks this step, and without it no message is ever delivered.
- Click **Test connection** to confirm Bot Framework accepts the credentials.

**Test connection** and **Download Teams app** stay disabled until the chatbot is enabled, both IDs are valid, a client secret is stored or a new one typed in (an open but empty secret box does not count), and the messaging endpoint the tab resolves — the **Base URL**, or your override if you set one — is HTTPS. If they are greyed out, one of those is missing. Saved changes reach the Teams bridge within about 30 seconds; nothing needs restarting. The **How to set up the Teams chatbot** card to the right of the fields summarises the same path in five steps.

## Publish the app to Teams

- Click **Download Teams app** to get `assetatlas-teams.zip`.
- Upload it under **Teams Admin Center → Manage apps → Upload custom app**, then approve it for the audience you want — org-wide or specific groups.
- Users find AssetAtlas in the Teams app catalog and open a personal chat with it.

## What users do on first message

The first message a user sends is never answered — it triggers account linking instead. Tell users this before announcing the bot, or the unanswered first message gets reported as a fault.

- The user sends the bot a direct message.
- The bot replies with a **Link your AssetAtlas account** card instead of an answer.
- The user clicks **Link my AssetAtlas account**. The page that opens asks them to confirm — it has no sign-in form of its own, so a user who is not already signed in to AssetAtlas in that browser is told to open AssetAtlas in another tab, sign in, and come back and click **Re-check**.
- The user clicks **Confirm link**.
- The user sends the question again. From then on the bot answers directly.
- Replies arrive as one message after a typing indicator; charts and tables follow as separate cards.

Linking binds a Teams identity to one AssetAtlas account and the bot then acts as that user, so everyone using it needs an AssetAtlas login of their own.

## Gotchas

- **A passing Test connection does not mean Teams will work.** The probe only asks Bot Framework whether it accepts the credentials — it does not check that the **Microsoft Teams** channel was added in Azure, nor that Bot Framework can reach your messaging endpoint. With the channel missing, every field validates, **Test connection** reports *Connection ok*, the app package downloads and sideloads, and no message is ever delivered. Every visible signal says success.
- **A passing Test connection does not save the typed secret.** The probe tests what is on screen — a typed, not-yet-saved client secret is the one being checked, matching the *Chatbot Configuration* tab — so a rotated secret can be verified before it is committed. But testing stores nothing: until you click the secret's **Save** button, the bridge keeps using the previously saved value.
- **`Connection failed: AADSTS7000215`** means Entra rejected the client secret — typically rotated, expired, or pasted with leading whitespace. Azure Bot secrets expire, so a bot that has worked for months can start failing with nothing changed on this tab. Click the pencil icon and save a fresh secret.
- **`Connection failed: bridge unreachable` or `bridge error` is not an Azure problem.** Both mean AssetAtlas could not get a verdict out of its own `assetatlas-teams-bridge` container — unreachable in the first case, erroring in the second. Check that the container is running and read its logs.
- **`teams_bridge_disabled=1` in the bridge logs is the normal idle state.** The bridge container ships with every deployment but only connects to Bot Framework once configured: `reason=toggle_off` means **Enable Microsoft Teams chatbot** is unticked, `reason=credentials_missing` means it is ticked but a credential field is empty.
- **A user who never clicks Confirm link stays in a loop.** Until the link is confirmed the bot has no AssetAtlas account to act as, so it answers every message with the linking card again rather than with an answer.
- **The linking card is single-use and expires after five minutes.** That is easily shorter than the detour of signing in first, so a user who takes their time gets an error page or *Linking failed* instead of a confirmation — and the same card cannot be reused once it has worked. The fix is never to retry the old card: send the bot another message and use the fresh one.
- **An HTTPS override does not stand in for an HTTPS Base URL.** The two buttons unlock on whichever URL the **Messaging endpoint** field resolves to, but the app package is built from the **Base URL** alone. With an HTTPS override over a **Base URL** that is missing or not HTTPS, **Download Teams app** is clickable and fails with a bare *Download failed … (400)* — the real reason (the **Base URL**) never reaches the screen.
