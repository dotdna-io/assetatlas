// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect } from 'react';

const SUFFIX = 'AssetAtlas';

export function useDocumentTitle(title: string) {
  useEffect(() => {
    document.title = title ? `${title} | ${SUFFIX}` : SUFFIX;
    return () => {
      document.title = SUFFIX;
    };
  }, [title]);
}
