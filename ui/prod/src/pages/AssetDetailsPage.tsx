// SPDX-License-Identifier: AGPL-3.0-or-later
import { useSearchParams } from 'react-router-dom';
import { useAssetDetail } from '@/api/asset-details';
import { useDocumentTitle } from '@/hooks';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { AssetHeader } from './asset-details/AssetHeader';
import { InfoCards } from './asset-details/InfoCards';
import { ConfidenceBar } from './asset-details/ConfidenceBar';
import { DataSources } from './asset-details/DataSources';
import styles from './asset-details/asset-details.module.css';

export default function AssetDetailsPage() {
  const [searchParams] = useSearchParams();
  const assetId = searchParams.get('id');

  const { data, isLoading, error } = useAssetDetail(assetId);

  useDocumentTitle(data?.asset?.hostname ?? data?.asset?.fqdn ?? data?.asset?.id ?? 'Asset');

  if (!assetId) {
    return (
      <div className={styles.errorWrap}>
        <p className={styles.errorTitle}>No asset ID provided</p>
        <p>Use the Explorer to find and select an asset.</p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className={styles.loadingWrap}>
        <LoadingSpinner />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className={styles.errorWrap}>
        <p className={styles.errorTitle}>Failed to load asset</p>
        <p>The asset may not exist or the server is unavailable.</p>
      </div>
    );
  }

  const { asset, providers, other_sources } = data;

  return (
    <div>
      <AssetHeader asset={asset} />
      <InfoCards asset={asset} providers={providers} />
      <ConfidenceBar asset={asset} />
      <DataSources providers={providers} otherSources={other_sources} />
    </div>
  );
}
