// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import { assetUrl } from '@/basePath';
import styles from './HelpPage.module.css';

export function HelpImage({ src, alt }: { src?: string; alt?: string }) {
  const [zoom, setZoom] = useState(false);
  if (!src) return null;
  const url = /^https?:\/\//i.test(src) ? src : assetUrl(src);
  return (
    <>
      <img
        src={url}
        alt={alt ?? ''}
        className={styles.shot}
        onClick={() => setZoom(true)}
      />
      {zoom && (
        <div
          data-testid="help-lightbox"
          className={styles.lightbox}
          onClick={() => setZoom(false)}
        >
          <img src={url} alt={alt ?? ''} className={styles.lightboxImg} />
        </div>
      )}
    </>
  );
}
