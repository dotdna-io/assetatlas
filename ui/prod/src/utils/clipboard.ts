// SPDX-License-Identifier: AGPL-3.0-or-later
import type { RefObject } from 'react';

/**
 * Copy `text` to the system clipboard using the async Clipboard API,
 * falling back to selecting the contents of `fallbackRef` if the API
 * is unavailable (older browsers, insecure contexts).
 */
export async function copyToClipboard(
  text: string,
  fallbackRef?: RefObject<HTMLInputElement | HTMLTextAreaElement>,
): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    fallbackRef?.current?.select();
  }
}

/**
 * Trigger a browser download of `text` as a plain-text file named `filename`.
 */
export function downloadTextFile(text: string, filename: string): void {
  const blob = new Blob([text], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * Trigger a browser download of an already-built `blob` as `filename`.
 * Use for binary/streamed responses fetched with auth (e.g. apiClient.download),
 * where the data is a Blob rather than a string.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
