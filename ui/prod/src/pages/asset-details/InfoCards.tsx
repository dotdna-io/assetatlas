// SPDX-License-Identifier: AGPL-3.0-or-later
import type { AssetDetail, AssetProvider } from '@/api/asset-details';
import { DataSourceBadge } from '@/components/ui/DataSourceBadge';
import styles from './asset-details.module.css';

interface InfoCardsProps {
  asset: AssetDetail;
  providers: AssetProvider[];
}

// ---------------------------------------------------------------------------
// Posture check definitions per category
// ---------------------------------------------------------------------------

interface PostureCheck {
  label: string;
  fieldKey: string; // field name in provider fields to look for
}

const POSTURE_CHECKS: Record<string, PostureCheck[]> = {
  workstation: [
    { label: 'Endpoint Security', fieldKey: 'edr' },
    { label: 'Domain Joined', fieldKey: 'domain' },
    { label: 'Device Management', fieldKey: 'management' },
    { label: 'Vulnerability Scanning', fieldKey: 'vulnerability' },
    { label: 'Monitoring', fieldKey: 'monitoring' },
  ],
  server: [
    { label: 'Endpoint Security', fieldKey: 'edr' },
    { label: 'Domain Joined', fieldKey: 'domain' },
    { label: 'Vulnerability Scanning', fieldKey: 'vulnerability' },
    { label: 'Monitoring', fieldKey: 'monitoring' },
  ],
  network: [
    { label: 'Vulnerability Scanning', fieldKey: 'vulnerability' },
    { label: 'Monitoring', fieldKey: 'monitoring' },
  ],
  mobile: [
    { label: 'Device Management', fieldKey: 'management' },
  ],
};

function getPostureStatus(
  check: PostureCheck,
  providers: AssetProvider[],
): { pass: boolean; providers: string[] } {
  const categoryMap: Record<string, string[]> = {
    edr: ['edr', 'endpoint security', 'endpoint protection'],
    vulnerability: ['vulnerability', 'vuln', 'scanner'],
    domain: ['directory', 'ad', 'ldap', 'active directory'],
    management: ['mdm', 'device management', 'uem'],
    monitoring: ['monitoring', 'siem', 'observability'],
  };

  const keywords = categoryMap[check.fieldKey] || [];
  const matched: string[] = [];
  for (const p of providers) {
    const cat = (p.source_category || '').toLowerCase();
    const type = (p.source_type || '').toLowerCase();
    const name = (p.source_name || '').toLowerCase();
    if (keywords.some((k) => cat.includes(k) || type.includes(k) || name.includes(k))) {
      matched.push(p.source_name || p.source_type);
    }
  }
  return { pass: matched.length > 0, providers: matched };
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function FieldRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className={styles.fieldRow}>
      <span className={styles.fieldLabel}>{label}</span>
      <span className={styles.fieldValue}>{value || '-'}</span>
    </div>
  );
}

function GeoField({ label, values }: { label: string; values: string[] }) {
  if (values.length === 0) return null;
  return (
    <div className={styles.fieldRow}>
      <span className={`${styles.fieldLabel} ${styles.geoLabel}`}>
        {label}
        <span className={styles.geoInfoWrap}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="11" height="11" style={{ opacity: 0.5, verticalAlign: 'middle' }}>
            <circle cx="12" cy="12" r="10" /><line x1="12" y1="16" x2="12" y2="12" /><line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
          <span className={styles.geoTooltip}>IP Geolocation by DB-IP (db-ip.com) — CC BY 4.0</span>
        </span>
      </span>
      <div className={styles.fieldValueList}>
        {values.map((v, i) => (
          <span key={i} className={styles.fieldValue}>{v}</span>
        ))}
      </div>
    </div>
  );
}

function MultiValueField({ label, values }: { label: string; values: string[] }) {
  if (values.length === 0) return <FieldRow label={label} value="-" />;
  return (
    <div className={styles.fieldRow}>
      <span className={styles.fieldLabel}>{label}</span>
      <div className={styles.fieldValueList}>
        {values.map((v, i) => (
          <span key={i} className={styles.fieldValue}>{v}</span>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------------

function GeneralInfoCard({ asset }: { asset: AssetDetail }) {
  return (
    <div className={styles.card}>
      <h3 className={styles.cardTitle}>General Information</h3>
      <div className={styles.cardBody}>
        {asset.hostnames.length > 1 ? (
          <MultiValueField label="Hostnames" values={asset.hostnames} />
        ) : (
          <FieldRow label="Hostname" value={asset.hostname} />
        )}
        <FieldRow label="FQDN" value={asset.fqdn} />
        <FieldRow label="Category" value={asset.category} />
        <FieldRow label="Sub Category" value={asset.subcategory} />
        {asset.vendor_category && <FieldRow label="Vendor Category" value={asset.vendor_category} />}
        <FieldRow label="OS" value={asset.os} />
        <FieldRow label="Serial Number" value={asset.sn} />
        {asset.owners.length > 1 ? (
          <MultiValueField label="Owners" values={asset.owners} />
        ) : (
          asset.owner && <FieldRow label="Owner" value={asset.owner} />
        )}
        {asset.users.length > 1 ? (
          <MultiValueField label="Users" values={asset.users} />
        ) : (
          asset.user && <FieldRow label="User" value={asset.user} />
        )}
        {asset.locations.length > 1 ? (
          <MultiValueField label="Locations" values={asset.locations} />
        ) : (
          asset.location && <FieldRow label="Location" value={asset.location} />
        )}
        {asset.ipam_locations.length > 1 ? (
          <MultiValueField label="IPAM Locations" values={asset.ipam_locations} />
        ) : (
          asset.ipam_location && <FieldRow label="IPAM Location" value={asset.ipam_location} />
        )}
      </div>
    </div>
  );
}

function NetworkCard({ asset }: { asset: AssetDetail }) {
  // Separate private vs public IPs if AssetAtlas extended_attributes provides them
  const aa = asset.extended_attributes?.AssetAtlas as Record<string, unknown> | undefined;
  const privateIps = (aa?.ip_private as string[]) || asset.ip;
  const publicIps = (aa?.ip_public as string[]) || [];
  const geoCountries = (aa?.geo_countries as string[]) || [];
  const geoCities = (aa?.geo_cities as string[]) || [];
  const geoRegions = (aa?.geo_regions as string[]) || [];

  return (
    <div className={styles.card}>
      <h3 className={styles.cardTitle}>Network Details</h3>
      <div className={styles.cardBody}>
        <MultiValueField label="Private IP" values={privateIps} />
        <MultiValueField label="MAC Address" values={asset.mac} />
        <FieldRow label="MAC Vendor" value={asset.mac_vendor} />
        <MultiValueField label="Public IP" values={publicIps} />
        {geoCountries.length > 0 && (
          <GeoField label="Country" values={geoCountries} />
        )}
        {geoRegions.length > 0 && (
          <GeoField label="Region" values={geoRegions} />
        )}
        {geoCities.length > 0 && (
          <GeoField label="City" values={geoCities} />
        )}
      </div>
    </div>
  );
}

function PostureCard({ asset, providers }: { asset: AssetDetail; providers: AssetProvider[] }) {
  const categoryKey = (asset.category || '').toLowerCase();
  const checks = POSTURE_CHECKS[categoryKey] || [];

  if (checks.length === 0) {
    return (
      <div className={styles.card}>
        <h3 className={styles.cardTitle}>Asset Posture</h3>
        <div className={styles.cardBody}>
          <span className={styles.fieldLabel}>No posture checks for this category</span>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.card}>
      <h3 className={styles.cardTitle}>Asset Posture</h3>
      <div className={styles.cardBody}>
        {checks.map((check) => {
          const result = getPostureStatus(check, providers);
          return (
            <div key={check.label} className={styles.postureRow}>
              <span className={result.pass ? styles.posturePass : styles.postureFail}>
                {result.pass ? (
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" width="14" height="14">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                ) : (
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" width="14" height="14">
                    <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                  </svg>
                )}
              </span>
              <span className={styles.postureLabel}>{check.label}</span>
              <div className={styles.postureProviders}>
                {result.providers.map((name) => (
                  <DataSourceBadge key={name} name={name} className={styles.postureBadge} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

export function InfoCards({ asset, providers }: InfoCardsProps) {
  return (
    <div className={styles.cardsGrid}>
      <GeneralInfoCard asset={asset} />
      <NetworkCard asset={asset} />
      <PostureCard asset={asset} providers={providers} />
    </div>
  );
}
