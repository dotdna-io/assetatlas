# Changelog

Notable changes to AssetAtlas, newest first.

Versions follow `YYYY.WW.N` — the year, the ISO week number, and the release
number within that week. See [CONTRIBUTING.md](CONTRIBUTING.md#versioning).

Release notes for each version are also on the
[Releases](https://github.com/dotdna-io/assetatlas/releases) page.

## 2026.40.1 — 2026-09-29

### Improved
- The MCP server shares one database connection pool across all connected
  assistants instead of opening one per connection.
- The MCP server starts even when the database is not yet reachable, and
  accepts API keys as soon as the database becomes available, without a
  restart.

### Fixed
- AI assistants connecting to the MCP server over HTTPS are no longer
  rejected with an invalid-host error when AssetAtlas runs behind its
  bundled reverse proxy.
- An assistant authenticating with an API key can connect to the MCP server
  when no other assistant is connected.
- One assistant disconnecting from the MCP server no longer interrupts the
  others.
- The MCP server no longer holds on to connections that have ended, so its
  memory use stays flat for clients that reconnect for every query.
- A request to the MCP address with a trailing slash is redirected to the
  same HTTPS address and port instead of to plain HTTP.

## 2026.37.3 — 2026-09-10

### Maintenance
- No user-facing changes; internal build, test, and release tooling only.

## 2026.37.2 — 2026-09-10

### Improved
- An update that skips one or more intermediate versions asks for
  confirmation instead of stopping with an error.

### Fixed
- A database backup that fails during an update is reported and halts the
  update, rather than being passed over silently.

## 2026.37.1 — 2026-09-09

### Added
- Unified field values are precomputed and stored as the asset pipeline
  runs, so Explorer filters and value lists no longer derive them on every
  request.

### Improved
- Explorer sorting is backed by indexes matching the sort orders the
  interface offers, removing full table scans on large inventories.
- Filter value lookups are served from a cache keyed to the field catalogue
  and the current asset data, and are refreshed as soon as either changes.
- Typeahead in the filter builder waits for a pause in typing, reuses
  results it has already fetched, and cancels requests it has superseded.
- The API serves requests with a threaded worker model, improving
  concurrency when interactive use overlaps a pipeline run.
- An API key's last-used timestamp is written at most once a minute per
  key, removing a database write from every authenticated request.

### Fixed
- Filter value counts honour the active search term instead of counting the
  unfiltered set.
- The metrics endpoint no longer blocks while the asset pipeline holds its
  table lock.

## 2026.32.15 — 2026-08-08

### Improved
- Deployments check for a newer release automatically every six hours,
  rather than only when an administrator opens the update page.

## 2026.32.14 — 2026-08-07

### Improved
- The health endpoint answers without initialising the machine-learning
  stack, so monitoring probes no longer allocate the embedding model.
- The embedding model is loaded once per worker instead of twice, halving
  the memory it occupies.
- Running the installer against an existing deployment asks for
  confirmation before it continues as an update.

### Fixed
- Online installations pin the exact container images published for the
  release being installed, instead of following the `latest` tag and
  potentially running a different build.
- An update that completed successfully is no longer reported as failed by
  the version check that runs after it.

## 2026.32.13 — 2026-08-05

### Maintenance
- No user-facing changes; internal build, test, and release tooling only.

## 2026.32.12 — 2026-08-05

### Added
- AssetAtlas checks for newer releases and reports its installed version to
  the update service, so administrators are told when an upgrade is
  available.
- The pagination limit an integration applies while collecting is now an
  operator setting rather than a fixed value in code.

### Fixed
- A collection run that reaches its pagination limit is reported as a
  partial sync. Previously it completed as though it had retrieved
  everything, leaving an inventory quietly short of records.

## 2026.32.11 — 2026-08-04

### Maintenance
- No user-facing changes; internal build, test, and release tooling only.

