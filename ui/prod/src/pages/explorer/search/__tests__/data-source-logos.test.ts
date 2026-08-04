// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, it, expect } from 'vitest';

import {
  resolveDataSourceLogo,
  DEFAULT_BRAND_COLOR,
} from '../data-source-logos';

const NONE = new Map<string, string>();

describe('resolveDataSourceLogo', () => {
  it('resolves a renamed source via the backend logo-map (name → logo path)', () => {
    // "Acme Intune" doesn't match any static key, but the integrations logo-map
    // carries its real logo. Brand colour is recovered from the logo filename.
    const logoByName = new Map([['acme intune', '/logos/intune.png']]);
    expect(resolveDataSourceLogo('Acme Intune', logoByName, NONE)).toEqual({
      logo: '/logos/intune.png',
      color: '#0078D4',
    });
  });

  it('falls back to the static brand map when the source has no logo-map entry', () => {
    expect(resolveDataSourceLogo('SentinelOne', NONE, NONE)).toEqual({
      logo: 'sentinel one.webp',
      color: '#6C2EB9',
    });
  });

  it('resolves a grouped source via its integration_type', () => {
    const typeByName = new Map([['corp endpoints', 'crowdstrike']]);
    expect(resolveDataSourceLogo('Corp Endpoints', NONE, typeByName)).toEqual({
      logo: 'crowdstrike.png',
      color: '#E11029',
    });
  });

  it('prefers the logo-map (custom/uploaded logo) over the static brand match', () => {
    const logoByName = new Map([
      ['sentinelone', '/api/integrations/logos/file/custom.png'],
    ]);
    expect(resolveDataSourceLogo('SentinelOne', logoByName, NONE)).toEqual({
      logo: '/api/integrations/logos/file/custom.png',
      color: DEFAULT_BRAND_COLOR,
    });
  });

  it('returns null for an unknown source (caller shows the letter fallback)', () => {
    expect(resolveDataSourceLogo('Mystery Source', NONE, NONE)).toBeNull();
  });
});
