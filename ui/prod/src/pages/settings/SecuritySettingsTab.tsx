// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from 'react';
import {
  settingsApi,
  useCategorySettings,
  useSettingsSchema,
  type SecurityEffectiveResponse,
} from '@/api/settings';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { CategorySettings } from './CategorySettings';

/**
 * Security tab — uses the generic schema-driven CategorySettings.
 *
 * Public Base URL gets two pieces of UX help:
 *   - The full description (incl. the operational checklist for what to
 *     update by hand after a rename) lives on the hover tooltip of the
 *     setting name. See descriptionAsTooltip.
 *   - The input pre-fills with the resolver's effective value (env-var
 *     fallback when the DB row is empty) so the operator sees the
 *     install's bootstrap URL instead of a blank field, and can save
 *     it as-is or edit before saving.
 */
export function SecuritySettingsTab() {
  const [effective, setEffective] =
    useState<SecurityEffectiveResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    settingsApi
      .getSecurityEffective()
      .then((data) => {
        if (!cancelled) setEffective(data);
      })
      .catch(() => {
        // Non-fatal: the input simply won't pre-fill from env. The
        // saved DB value still renders normally via CategorySettings.
        if (!cancelled) setEffective(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const effectiveValues = effective
    ? { public_base_url: effective.public_base_url.value }
    : undefined;

  // Pre-fetch schema + both category data sets at the page level so we can
  // show a single LoadingSpinner. CategorySettings reads the same React-Query
  // cache, so it skips its own internal spinner once these resolve — which
  // avoids the visual stacking of two spinners while both cards mount.
  const { isLoading: schemaLoading } = useSettingsSchema();
  const { isLoading: securityLoading } = useCategorySettings('security');
  const { isLoading: authLoading } = useCategorySettings('auth');

  if (schemaLoading || securityLoading || authLoading) {
    return <LoadingSpinner />;
  }

  return (
    <>
      <CategorySettings
        category="security"
        layout="stack"
        effectiveValues={effectiveValues}
      />
      <CategorySettings
        category="auth"
        layout="stack"
      />
    </>
  );
}
