// SPDX-License-Identifier: AGPL-3.0-or-later
import { memo, isValidElement, Children } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { HelpImage } from './HelpImage';

const ALLOWED_ELEMENTS = [
  'p', 'br', 'strong', 'em', 'del', 'a', 'img',
  'ul', 'ol', 'li', 'code', 'pre', 'blockquote', 'hr',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'table', 'thead', 'tbody', 'tr', 'th', 'td',
] as const;

// Named functions so the p override can compare against imgRenderer by
// reference — react-markdown v9 resolves component overrides before
// passing children to parent component overrides, so inside the `p`
// handler the sole child of an image paragraph has type === imgRenderer.
function imgRenderer({ src, alt }: { src?: string; alt?: string }) {
  return <HelpImage src={typeof src === 'string' ? src : undefined} alt={alt} />;
}

/** True when the paragraph's sole child is the img component override.
 *  React-markdown v9 resolves component overrides before calling parent
 *  component overrides, so inside `p`, an image paragraph arrives with
 *  children[0].type === imgRenderer. imgRenderer returns a <div>
 *  (placeholder) or a fragment with a <div> (lightbox) — both invalid
 *  inside <p>. We unwrap by rendering the children without the <p>. */
function isImageOnly(children: React.ReactNode): boolean {
  const arr = Children.toArray(children);
  return arr.length === 1 && isValidElement(arr[0]) && arr[0].type === imgRenderer;
}

const components: Components = {
  a: ({ href, children, ...rest }) => (
    <a {...rest as Record<string, unknown>} href={href}
       target="_blank" rel="noopener noreferrer">{children}</a>
  ),
  img: imgRenderer,
  // Prevent <div>-in-<p> DOM nesting warning: when a paragraph's only
  // child is an image node (rendered by HelpImage as a <div>), skip the <p>.
  p: ({ children, ...rest }) =>
    isImageOnly(children)
      ? <>{children}</>
      : <p {...rest as Record<string, unknown>}>{children}</p>,
};

function HelpMarkdownInner({ content }: { content: string }) {
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

export const HelpMarkdown = memo(HelpMarkdownInner);
