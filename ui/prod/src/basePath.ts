// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Runtime base path detection for reverse-proxy deployments.
 *
 * When deployed behind a prefix (e.g., /assetatlas/), the Express server
 * sets a <meta> tag with the base path. Falls back to '' for local dev.
 */
const meta = document.querySelector<HTMLMetaElement>('meta[name="base-path"]');
export const BASE_PATH = meta?.content?.replace(/\/$/, '') || '';

/** Prefix an absolute path with the base path. No-op in local dev. */
export function assetUrl(path: string): string {
  return BASE_PATH + path;
}
