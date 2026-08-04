// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAssetHistory } from '@/api/asset-history';
import { useAssetDetail } from '@/api/asset-details';
import { useDocumentTitle } from '@/hooks';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { EventTimeline } from '@/components/ui/EventTimeline';
import { HistoryHeader } from './asset-history/HistoryHeader';
import { HistoryTable } from './asset-history/HistoryTable';
import styles from './asset-history/asset-history.module.css';

export default function AssetHistoryPage() {
  const [searchParams] = useSearchParams();
  const assetId = searchParams.get('id');
  const [timeRange, setTimeRange] = useState('7d');
  const [drillDate, setDrillDate] = useState<string | null>(null);
  const [drillEventIds, setDrillEventIds] = useState<Set<string> | null>(null);

  const { data, isLoading, error } = useAssetHistory(assetId, timeRange);
  const { data: assetDetail } = useAssetDetail(assetId);

  useDocumentTitle('History: ' + (assetDetail?.asset?.hostname ?? assetDetail?.asset?.id ?? 'Asset'));

  // When the user clicks a chart bucket, restrict the table to those events.
  // Re-clicking the same bucket clears the filter (toggle).
  const handleBucketClick = (date: string, events: { id: string }[]) => {
    if (drillDate === date) {
      setDrillDate(null);
      setDrillEventIds(null);
    } else {
      setDrillDate(date);
      setDrillEventIds(new Set(events.map((e) => e.id)));
    }
  };

  const visibleEvents = useMemo(() => {
    if (!data) return [];
    if (drillEventIds) return data.events.filter((e) => drillEventIds.has(e.id));
    return data.events;
  }, [data, drillEventIds]);

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
        <p className={styles.errorTitle}>Failed to load history</p>
        <p>The asset may not exist or the server is unavailable.</p>
      </div>
    );
  }

  return (
    <div>
      <HistoryHeader
        asset={data.asset}
        assetDetail={assetDetail?.asset ?? null}
        timeRange={timeRange}
        onTimeRangeChange={setTimeRange}
      />
      <div style={{ marginBottom: 32 }}>
        <EventTimeline events={data.events} onBucketClick={handleBucketClick} />
      </div>
      <HistoryTable
        events={visibleEvents}
        drillDate={drillDate}
        onClearDrill={() => {
          setDrillDate(null);
          setDrillEventIds(null);
        }}
      />
    </div>
  );
}
