// SPDX-License-Identifier: AGPL-3.0-or-later
/** "A", "A and B", "A, B and C" — shared by the settings master-toggle notes
 *  (CategorySettings and the bespoke tabs) so every one of them lists its
 *  unconfigured fields in the same words. */
export function joinLabels(labels: string[]): string {
  if (labels.length <= 1) return labels.join('');
  return `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`;
}

export function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}
