// SPDX-License-Identifier: AGPL-3.0-or-later
import { memo } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { CategoryTag } from '@/components/ui/CategoryTag';
import { DataSourceBadge } from '@/components/ui/DataSourceBadge';
import { isKnownCategory } from '@/components/ui/CategoryIcon';
import { getDataSourceLogo } from '@/pages/explorer/search/data-source-logos';

interface Props {
  content: string;
}

/** Whitelist of allowed markdown HTML tag names. Anything else (raw
 *  HTML, scripts, iframes) is dropped via `allowedElements`. */
const ALLOWED_ELEMENTS = [
  'p', 'br', 'strong', 'em', 'del', 'a',
  'ul', 'ol', 'li',
  'code', 'pre',
  'blockquote', 'hr',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'table', 'thead', 'tbody', 'tr', 'th', 'td',
] as const;


/** Strict whole-string match. The cell or paragraph value must IS a
 *  known category or data source — names that appear as part of other
 *  text (e.g. "Server" inside "Windows Server 2012") are intentionally
 *  not badged, since the same string in an OS / hostname / model
 *  column would otherwise produce false-positive badges. */
function maybeBadge(text: string): React.ReactNode {
  const trimmed = text.trim();
  if (!trimmed) return text;
  if (isKnownCategory(trimmed)) {
    return <CategoryTag category={trimmed} />;
  }
  if (getDataSourceLogo(trimmed) != null) {
    return <DataSourceBadge name={trimmed} tinted />;
  }
  return text;
}


/** Apply maybeBadge only when the element's children collectively are
 *  one text node — i.e. a self-contained cell value or list item value.
 *  Mixed children (e.g. <strong>Server</strong>: 773) are passed
 *  through untransformed. */
function transformChildren(children: React.ReactNode): React.ReactNode {
  if (children == null) return children;
  if (typeof children === 'string') return maybeBadge(children);
  if (Array.isArray(children) && children.length === 1 && typeof children[0] === 'string') {
    return maybeBadge(children[0]);
  }
  return children;
}


// Component overrides: leaf text containers run children through the
// badge transformer. Code spans / pre blocks are NOT overridden so the
// transformer never touches text inside them.
const components: Components = {
  p:  ({ children, ...rest }) =>
    <p {...rest as Record<string, unknown>}>
      {transformChildren(children)}
    </p>,
  li: ({ children, ...rest }) =>
    <li {...rest as Record<string, unknown>}>
      {transformChildren(children)}
    </li>,
  td: ({ children, ...rest }) =>
    <td {...rest as Record<string, unknown>}>
      {transformChildren(children)}
    </td>,
  th: ({ children, ...rest }) =>
    <th {...rest as Record<string, unknown>}>
      {transformChildren(children)}
    </th>,
  // External links open in a new tab. The href is sanitized by
  // react-markdown by default (no javascript:/data: URIs).
  a: ({ href, children, ...rest }) => (
    <a
      {...rest as Record<string, unknown>}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
    >
      {children}
    </a>
  ),
  // code, pre: NOT overridden — react-markdown's defaults render them
  // as <code> / <pre><code>, which is exactly what we want. Critically,
  // we do not apply the badge transformer there.
};


function AssistantMarkdownInner({ content }: Props) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      allowedElements={ALLOWED_ELEMENTS}
      unwrapDisallowed
      components={components}
    >
      {content}
    </ReactMarkdown>
  );
}

export const AssistantMarkdown = memo(AssistantMarkdownInner);
