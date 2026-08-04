// SPDX-License-Identifier: AGPL-3.0-or-later
// Renders the vendor logo for an OIDC provider next to the SSO button label.
// Provider kind is detected on the backend from the discovery URL (see
// OidcService.provider_kind). For kinds we have a logo asset for, we render
// an <img> tag with a small white plate so the colour mark reads on the
// blue button. For everything else we fall back to a stroked key icon.
import { assetUrl } from '@/basePath';
import type { SsoProviderKind } from '@/api/auth';
import styles from '@/pages/LoginPage.module.css';

const PROVIDER_LOGOS: Partial<Record<SsoProviderKind, string>> = {
  entra: '/logos/microsoft-entra-id.png',
  aws: '/logos/aws.png',
  google: '/logos/google.png',
  okta: '/logos/OKTA.png',
};

export function SsoProviderIcon({ kind }: { kind: SsoProviderKind }) {
  const logo = PROVIDER_LOGOS[kind];
  if (logo) {
    return (
      <img
        src={assetUrl(logo)}
        alt=""
        aria-hidden="true"
        className={styles.ssoIcon}
      />
    );
  }
  // Generic SSO key icon — used for okta / cyberark / unknown providers.
  return (
    <svg
      className={styles.ssoIconStroke}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
    </svg>
  );
}
