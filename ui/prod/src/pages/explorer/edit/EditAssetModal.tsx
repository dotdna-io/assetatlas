// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from 'react';
import { Modal } from '@/components/ui/Modal';
import { ReclassifyTab } from './ReclassifyTab';
import { TagTab } from './TagTab';
import { MergeTab } from './MergeTab';
import { SplitTab } from './SplitTab';
import { AssetLabelTab } from './AssetLabelTab';
import styles from './edit.module.css';

type Tab = 'reclassify' | 'tag' | 'merge' | 'split' | 'label';

const TABS: { id: Tab; label: string }[] = [
  { id: 'reclassify', label: 'Reclassify' },
  { id: 'tag', label: 'Tag Asset' },
  { id: 'merge', label: 'Merge Assets' },
  { id: 'split', label: 'Split Asset' },
  { id: 'label', label: 'Asset Label' },
];

export interface EditAssetModalProps {
  assetId: string | null;
  assetData?: Record<string, unknown>;
  initialTab?: Tab;
  onClose: () => void;
}

export function EditAssetModal({ assetId, assetData, initialTab, onClose }: EditAssetModalProps) {
  const [activeTab, setActiveTab] = useState<Tab>(initialTab ?? 'reclassify');

  // Re-apply the requested tab whenever a new asset opens (modal isn't remounted).
  useEffect(() => {
    if (assetId && initialTab) setActiveTab(initialTab);
  }, [assetId, initialTab]);

  const isOpen = assetId !== null;

  const handleSuccess = () => {
    // Keep the modal open after success — user may want to make further edits
  };

  const title = assetData?.hostname
    ? `Edit Asset: ${String(assetData.hostname)}`
    : 'Edit Asset';

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={title} size="lg" showCloseButton>
      <div className={styles.tabs}>
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={[styles.tab, activeTab === tab.id ? styles.tabActive : '']
              .filter(Boolean)
              .join(' ')}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {isOpen && assetId && (
        <>
          {activeTab === 'reclassify' && (
            <ReclassifyTab
              assetId={assetId}
              currentCategory={assetData?.category != null ? String(assetData.category) : undefined}
              currentSubcategory={
                assetData?.subcategory != null ? String(assetData.subcategory) : undefined
              }
              onSuccess={handleSuccess}
            />
          )}
          {activeTab === 'tag' && (
            <TagTab assetId={assetId} onSuccess={handleSuccess} />
          )}
          {activeTab === 'merge' && (
            <MergeTab assetId={assetId} assetData={assetData} onSuccess={handleSuccess} />
          )}
          {activeTab === 'split' && (
            <SplitTab assetId={assetId} assetData={assetData} onSuccess={handleSuccess} />
          )}
          {activeTab === 'label' && (
            <AssetLabelTab assetId={assetId} onSuccess={handleSuccess} />
          )}
        </>
      )}
    </Modal>
  );
}
