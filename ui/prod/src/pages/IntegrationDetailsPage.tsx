// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { useDocumentTitle } from '@/hooks';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { useIntegration, useRunIntegration, useDeleteIntegration, useTestConnection } from '@/api/integrations';
import { useAdaptivePolling } from '@/hooks/useAdaptivePolling';
import { DetailsHeader } from './integration-details/DetailsHeader';
import { HealthCard } from './integration-details/HealthCard';
import { ConnectionCard } from './integration-details/ConnectionCard';
import { ScheduleCard } from './integration-details/ScheduleCard';
import { BatchesTable } from './integration-details/BatchesTable';
import { MappingPreview } from './integration-details/MappingPreview';
import { RecentErrors } from './integration-details/RecentErrors';
import styles from './IntegrationDetailsPage.module.css';

export default function IntegrationDetailsPage() {
  const [searchParams] = useSearchParams();
  const id = searchParams.get('id') || undefined;
  const navigate = useNavigate();
  const [status, setStatus] = useInlineStatus();

  const { interval, triggerBurst } = useAdaptivePolling();
  // Use extended burst (10 min) for batch polling after Run Now
  const { interval: batchInterval, triggerBurst: triggerBatchBurst } = useAdaptivePolling(5000, 600000);

  const { data, isLoading } = useIntegration(id, { refetchInterval: interval });
  const integration = data?.integration;

  useDocumentTitle(data?.integration?.name ?? 'Integration');

  const runMutation = useRunIntegration({
    onSuccess: () => { triggerBurst(); triggerBatchBurst(); setStatus({ kind: 'success', message: 'Data collection started' }); },
  });

  const deleteMutation = useDeleteIntegration({
    onSuccess: () => { setStatus({ kind: 'success', message: 'Integration deleted' }); navigate('/integrations'); },
  });

  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'success' | 'failed'>('idle');
  const testConnectionMutation = useTestConnection({
    onMutate: () => setTestStatus('testing'),
    onSuccess: (result) => {
      setTestStatus(result.success ? 'success' : 'failed');
    },
    onError: () => setTestStatus('failed'),
  });

  function handleTestConnection() {
    if (!integration) return;
    testConnectionMutation.mutate(integration.id);
  }

  if (!id) return <div>No integration ID provided</div>;
  if (isLoading) return <LoadingSpinner />;
  if (!integration) return <div>Integration not found</div>;


  return (
    <div className={styles.page}>
      <InlineStatus status={status} />
      <DetailsHeader
        integration={integration}
        onRunNow={() => runMutation.mutate(integration.id)}
        onEdit={() => navigate(`/integration-wizard?mode=edit&id=${integration.id}`)}
        onDelete={() => setDeleteConfirmOpen(true)}
        isRunning={runMutation.isPending}
      />

      <div className={styles.overviewGrid}>
        <HealthCard integration={integration} refetchInterval={interval} />
        <ConnectionCard integration={integration} onTestConnection={handleTestConnection} testStatus={testStatus} />
        <ScheduleCard integration={integration} refetchInterval={interval} />
      </div>

      <BatchesTable integrationId={integration.id} refetchInterval={batchInterval} />

      <MappingPreview integrationId={integration.id} refetchInterval={interval} />
      <RecentErrors integrationId={integration.id} refetchInterval={interval} />

      <ConfirmDialog
        isOpen={deleteConfirmOpen}
        onConfirm={() => { deleteMutation.mutate(integration.id); setDeleteConfirmOpen(false); }}
        onCancel={() => setDeleteConfirmOpen(false)}
        title="Delete Integration"
        message={`Are you sure you want to delete "${integration.name}"? This action cannot be undone.`}
        variant="danger"
      />
    </div>
  );
}
