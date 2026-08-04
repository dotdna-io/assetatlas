// SPDX-License-Identifier: AGPL-3.0-or-later
import { assetUrl } from '@/basePath';
import { getDataSourceLogo } from '@/pages/explorer/search/data-source-logos';

/**
 * Resolve an integration `logo` field to a URL suitable for `<img src=…>`.
 *
 * The `logo` column persists in two shapes by design:
 *   - Bundled library logos store the bare filename ("jamf.png"); the UI
 *     container serves them at `/logos/<name>` (Vite publicDir → Express
 *     express.static in prod).
 *   - User-uploaded customs store a full API path
 *     ("/api/integrations/logos/file/<sha>.png"); the API container serves
 *     them off a persistent volume.
 *
 * The wizard's pre-save strip (`replace(/^\/logos\//, '')` in
 * IntegrationWizardPage) only touches the bundled prefix, so both shapes
 * round-trip cleanly through the database.
 *
 * Always pass the result through {@link assetUrl} so it survives reverse-
 * proxy mounts (e.g. /assetatlas/).
 */
export function resolveLogoUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  // External absolute URL — used by a few connectors that ship their own
  // CDN-hosted icons. Pass through untouched.
  if (/^https?:\/\//i.test(value)) return value;
  // Already a path (uploaded API URL, or any pre-prefixed value).
  if (value.startsWith('/')) return assetUrl(value);
  // Bare filename → bundled library logo.
  return assetUrl(`/logos/${value}`);
}

/**
 * Resolve the logo to display for a data source.
 *
 * Prefers the API-provided `logo` field (resolved by the backend from the
 * integration's persisted `logo` column) since it works for any
 * operator-chosen name. Falls back to the name-based hardcoded map for
 * sources without an integration record (e.g. legacy chronicle-only
 * sources or ad-hoc names that don't match an integration row).
 */
export function resolveSourceLogo(
  apiLogo: string | null | undefined,
  name: string,
): string | null {
  const fromApi = resolveLogoUrl(apiLogo);
  if (fromApi) return fromApi;
  const fallback = getDataSourceLogo(name);
  return fallback ? resolveLogoUrl(fallback.logo) : null;
}
