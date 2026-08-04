// SPDX-License-Identifier: AGPL-3.0-or-later
import { CategorySettings } from './CategorySettings';

/**
 * Chatbot → Behavior tab — generic schema-driven settings for the `chat`
 * category (the tool-routing toggle + top-k). Like SecuritySettingsTab, it's a
 * thin wrapper over CategorySettings.
 *
 * chat.active_provider lives in the same category but is intentionally not
 * shown here: it's marked hidden in the schema and owned by the bespoke
 * provider Configuration tab (via /api/chat/config). CategorySettings skips
 * hidden settings, so this tab renders only the routing controls.
 */
export function ChatBehaviorSettingsTab() {
  return <CategorySettings category="chat" />;
}
