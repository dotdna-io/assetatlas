# Updating AssetAtlas

AssetAtlas updates in place: one command backs up the database, applies any new migrations, swaps in the new images, restarts the services, and then checks that the result is healthy — undoing itself if it isn't. How you start that update depends on whether the host can reach the internet. Updates are run on the server by an operator with root access, not from this interface.

## Checking what you're running

The **About** page (**Settings → System → About**) is where to start, and where to come back to afterwards:

- **Version** and **Build Date** — exactly what is deployed right now. Quote both when reporting a problem.
- An *update available* badge beside the version means a newer release has been published. It tells you an update exists; applying it is the server-side procedure below.
- **Check for updates** (admin only) turns the automatic check on or off, and **Check now** runs it immediately. The check needs outbound internet access — on an air-gapped host leave it off and track new releases yourself.
- The rest of the page — **Health**, **Inventory**, **Storage** — is worth a look before and after an update. See *About*.

## Updating an internet-connected install

Run `sudo assetatlas update` on the server. With no flags it resolves the latest published version, compares it with what's installed, and, if something newer exists, prompts you: apply now, dry-run first, or cancel. The default is dry-run.

- `sudo assetatlas update --dry-run` previews only — the version delta, the migrations that would be applied, and the image plan. It changes nothing.
- `sudo assetatlas update --yes` applies without prompting, for scheduled or scripted updates.
- Re-running the online installer script is equally safe: it detects the existing install and hands off to the updater by itself, so a re-run updates rather than reinstalling over your data.

The command is `assetatlas`, installed at `/usr/local/bin/assetatlas`. `assetatlas-cli` still works as a compatibility alias.

## Updating an air-gapped install

`sudo assetatlas update` deliberately does **not** work without outbound network access, and it **fails with an error** rather than reporting "already up to date" — claiming an install is current when it simply could not check would be worse than saying nothing. A bare `installer/update.sh` behaves identically: it runs the same self-fetching front end and fails the same way.

The supported path is the offline bundle:

- Transfer the offline tarball to the host and extract it **inside the install directory** — `/opt/assetatlas` unless you chose another one at install time. The tarball unpacks with no top-level folder of its own, so it lands directly over the existing install tree, which is exactly what the updater expects.
- Run `installer/install.sh` from that directory. It detects the existing install and dispatches to `update.sh --apply-local`, which skips the download and applies the bundle you just extracted.
- `installer/update.sh --apply-local` does the same thing if you prefer to call the updater directly.
- Extracting somewhere else and running the installer from there does **not** update the host: the updater takes the version it is upgrading to, and the migrations it applies, from the install directory — so it ends up comparing the installed version against itself, reports that the host is already up to date, and exits without applying anything.

## What happens during an update

Whichever path you take, the same sequence runs — an update is not a black box:

- **Pre-flight checks** — free disk space, whether a pipeline run is in flight, and whether the TLS certificate has expired. A pipeline already running stops the update rather than interrupting the run; wait for it to finish and start again.
- **Backup and snapshot** — the database is dumped and the current state (configuration, compose files, image tags) is snapshotted *before* anything destructive happens.
- **Apply** — pending migrations run, new images are pulled (or loaded from the bundle on an air-gapped host), and the services restart.
- **Health window** — the updater polls the application's liveness endpoint, then the container health checks, for up to a couple of minutes.
- **Auto-rollback on failure** — if health does not come back within that window, the update restores the database, the previous state and the previous image tags, and restarts by itself. You end up back on the version you started from. If the restored stack still fails its health check, the updater stops and says so — that is the one case that needs a person.

Auto-rollback covers the health window and nothing before it. A failure at pre-flight, during a migration or on an image pull stops the update where it stands without restoring anything; if migrations had already run, the database is on the new schema while the previous images are still serving, and you recover with the manual rollback below.

Expect a short outage while the services restart. When it finishes, reload the **About** page: **Version** and **Build Date** should show the new release, and **Health** should be green across the board.

## Skipping versions

Versions are numbered by year and week, and updates are expected to move forward one week at a time. Any release within the same week, or in the week that follows, is accepted; a jump further ahead than that is refused as non-linear. If you are deliberately catching up across several weeks at once, pass `--allow-version-skip`.

## Rolling back later

If an update passes its health checks but proves bad hours later, you can still roll back by hand to the snapshot taken immediately before it. `sudo assetatlas rollback` with no argument uses the most recent snapshot, which is the one you want in almost every case. Snapshots are named after the version whose update created them, so if you name one explicitly, name the version you updated *to* — not the version you want to get back to. This restores the database as it was at that snapshot, so anything written since the update is lost.

## What to capture before asking for help

- The **About** page: **Version**, **Build Date**, the **Health** status of each subsystem, and the **Storage** figures. That snapshot answers most of the opening questions and shows whether the problem is the update itself or something underneath it.
- Whether the update ended in an auto-rollback — if it did, you are running the previous version, and the current **Version** will confirm it.
- A diagnostics bundle from the server (`assetatlas support-bundle`) collects logs, install state and a redacted configuration into a single file, with secrets scrubbed, so it is safe to send on.
