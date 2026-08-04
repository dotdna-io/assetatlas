// SPDX-License-Identifier: AGPL-3.0-or-later
import { useHealth } from '@/api/settings';

/**
 * Whether the chat backend is available.
 *
 * Reads `application.features.chat` from the shared `useHealth()` query so
 * we don't fire a parallel `/api/health` request (the sidebar version probe
 * already polls it on a 30s interval). User-facing chat routes 503 when
 * `chat_service` is None server-side; this flag mirrors that state so the
 * UI can hide the chat widget instead of mounting a panel that can't open.
 */
export function useChatAvailable(): boolean {
  const { data } = useHealth();
  return data?.application?.features?.chat === true;
}
