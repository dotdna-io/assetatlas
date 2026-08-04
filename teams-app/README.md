# AssetAtlas Teams app package

Operators no longer use this directory directly. The Teams app zip is
built and downloaded from **Settings → Chatbot → Teams** in the
AssetAtlas UI; site-specific Teams bot setup notes (Azure AD app
registration, manifest tenancy, etc.) live in your private ops repository.

This directory is retained for:

- CI sanity checks (`build.sh`).
- Local-dev testing where running the full API container is impractical.

## Build (CI / local-dev)

    cd teams-app
    TEAMS_BOT_APP_ID=<app-id> \
    ASSETATLAS_PUBLIC_BASE_URL=https://your-host \
    ./build.sh

Outputs `teams-app/assetatlas-teams.zip`. `ORG_NAME` is hardcoded to
`DOTDNA`; `developer.*` URLs derive from `ASSETATLAS_PUBLIC_BASE_URL`,
which Teams requires to be HTTPS.

## Icons

`icons/color.png` (192×192) and `icons/outline.png` (32×32) are the
defaults. Replace with custom artwork before publishing if needed.
