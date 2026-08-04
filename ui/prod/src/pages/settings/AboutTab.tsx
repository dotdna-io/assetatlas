// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/AboutTab.tsx
import {
  useHealth,
  useSuggestionsHealth,
  useHealthAbout,
  useUpdateCheck,
  useForceUpdateCheck,
  useSetUpdateCheckEnabled,
} from '@/api/settings';
import {
  SettingsSection,
  SettingsRowGrid,
  SettingsRow,
} from '@/components/settings';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch/ToggleSwitch';
import { formatTimestamp } from './utils';
import styles from './AboutTab.module.css';
import { useAuth } from '@/auth';

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return '-';
  return Number(n).toLocaleString();
}

type StatusLevel =
  | 'healthy'
  | 'degraded'
  | 'unhealthy'
  | 'disabled'
  | 'unavailable'
  | 'unknown'
  | string;

function StatusText({ status }: { status: StatusLevel }) {
  const colors: Record<string, string> = {
    healthy: '#10b981',
    ready: '#10b981',
    alive: '#10b981',
    running: '#10b981',
    degraded: '#f59e0b',
    unhealthy: '#ef4444',
    error: '#ef4444',
    disabled: '#94a3b8',
    'n/a': '#94a3b8',
    unavailable: '#94a3b8',
    unknown: '#94a3b8',
  };
  const color = colors[status?.toLowerCase()] ?? colors.unknown;
  return <span className={styles.statusText} style={{ color }}>{status}</span>;
}

export function AboutTab() {
  const { data: health } = useHealth();
  const { data: suggestions, isError: suggestionsError } = useSuggestionsHealth();
  const { data: about } = useHealthAbout();
  const { isAdmin } = useAuth();
  const { data: updateCheck } = useUpdateCheck();
  const forceCheck = useForceUpdateCheck();
  const setUpdateCheckEnabled = useSetUpdateCheckEnabled();

  const app = health?.application ?? {};
  const checks = health?.checks ?? {};

  let semanticStatus: StatusLevel = 'unavailable';
  if (!suggestionsError && suggestions) {
    if (suggestions.status === 'healthy') {
      semanticStatus = 'healthy';
    } else if (suggestions.model_loaded && suggestions.chromadb_available) {
      semanticStatus = 'healthy';
    } else if (suggestions.model_loaded || suggestions.chromadb_available) {
      semanticStatus = 'degraded';
    } else {
      semanticStatus = 'unavailable';
    }
  }

  const schedulerStatus = checks.scheduler?.status ?? 'unknown';
  const schedulerJobs = checks.scheduler?.jobs;

  const hasInventory = !!(about?.assets || about?.integrations);
  const hasStorage = !!(about?.database || about?.redis || about?.logs);

  return (
    <div className={styles.grid}>
      {/* Application — version/build/uptime + the runtime it's running on.
          Combines what was previously two separate cards (Application +
          Runtime) into one "what's deployed and where" card. */}
      <SettingsSection title="Application">
        <SettingsRowGrid>
          <SettingsRow
            name="Version"
            control={
              <span className={styles.versionCell}>
                <span className={styles.mono}>{app.version ?? 'unknown'}</span>
                {updateCheck?.update_available && (
                  <span className={styles.updateBadge}>
                    update available → {updateCheck.latest_version}
                  </span>
                )}
              </span>
            }
          />
          <SettingsRow
            name="Build Date"
            control={<span className={styles.mono}>{app.build_date ?? 'unknown'}</span>}
          />
          {/* Admin-only master switch for the update check. Rendered even when
              off (that's the point — there is no other UI to enable it). Full
              width so the toggle + Check-now + status sit on their own line
              directly under the version they act on. */}
          {isAdmin && (
            <SettingsRow
              name="Check for updates"
              fullWidth
              control={
                <span className={styles.updateControl}>
                  <ToggleSwitch
                    checked={!!updateCheck?.enabled}
                    disabled={setUpdateCheckEnabled.isPending}
                    onChange={(v) => setUpdateCheckEnabled.mutate(v)}
                  />
                  {updateCheck?.enabled && (
                    <button
                      type="button"
                      className={styles.checkNow}
                      onClick={() => forceCheck.mutate()}
                      disabled={forceCheck.isPending}
                    >
                      {forceCheck.isPending ? 'Checking…' : 'Check now'}
                    </button>
                  )}
                  {forceCheck.isSuccess && updateCheck?.enabled && !updateCheck.update_available && (
                    <span className={styles.checkStatus}>
                      {updateCheck.latest_version
                        ? 'Up to date'
                        : 'Couldn’t reach release server'}
                    </span>
                  )}
                </span>
              }
            />
          )}
          <SettingsRow
            name="Uptime"
            control={
              <span className={styles.mono}>
                {app.uptime_seconds != null
                  ? formatUptime(app.uptime_seconds)
                  : 'unknown'}
              </span>
            }
          />
          {about?.runtime && (
            <>
              <SettingsRow
                name="Python"
                control={<span className={styles.mono}>{about.runtime.python_version}</span>}
              />
              <SettingsRow
                name="Platform"
                control={<span className={styles.mono}>{about.runtime.platform}</span>}
                fullWidth
              />
            </>
          )}
        </SettingsRowGrid>
      </SettingsSection>

      {/* Inventory — what's been ingested and where it came from. Merges
          asset totals (last pipeline output) with integration totals
          (where the next data is coming from). */}
      {hasInventory && (
        <SettingsSection title="Inventory">
          <SettingsRowGrid>
            {about?.assets && (
              <>
                <SettingsRow
                  name="Total Assets"
                  control={<span className={styles.mono}>{formatNumber(about.assets.total)}</span>}
                />
                <SettingsRow
                  name="Data Sources"
                  control={<span className={styles.mono}>{formatNumber(about.assets.data_sources)}</span>}
                />
                <SettingsRow
                  name="Last Pipeline Run"
                  control={
                    <span className={styles.mono}>
                      {formatTimestamp(about.assets.last_pipeline_run)}
                    </span>
                  }
                />
              </>
            )}
            {about?.integrations && (
              <>
                <SettingsRow
                  name="Connectors"
                  control={<span className={styles.mono}>{formatNumber(about.integrations.total)}</span>}
                />
                <SettingsRow
                  name="Active Connectors"
                  control={<span className={styles.mono}>{formatNumber(about.integrations.active)}</span>}
                />
                <SettingsRow
                  name="Next Sync"
                  control={
                    <span className={styles.mono}>
                      {about.integrations.next_sync
                        ? `${about.integrations.next_sync.name} — ${formatTimestamp(about.integrations.next_sync.at)}`
                        : '-'}
                    </span>
                  }
                />
              </>
            )}
          </SettingsRowGrid>
        </SettingsSection>
      )}

      {/* Health — connectivity checks for the four critical subsystems. */}
      <SettingsSection title="Health">
        <SettingsRowGrid>
          <SettingsRow
            name="Database"
            control={<StatusText status={checks.database?.status ?? 'unknown'} />}
          />
          <SettingsRow
            name="Cache"
            control={<StatusText status={checks.cache?.status ?? 'unknown'} />}
          />
          <SettingsRow
            name="Scheduler"
            control={
              <span className={styles.schedulerCell}>
                <StatusText status={schedulerStatus} />
                {schedulerJobs != null && (
                  <span className={styles.jobCount}>{schedulerJobs} jobs</span>
                )}
              </span>
            }
          />
          <SettingsRow
            name="Semantic Search"
            control={<StatusText status={semanticStatus} />}
          />
        </SettingsRowGrid>
      </SettingsSection>

      {/* Storage — where bytes are sitting on disk and in memory.
          Merges the previous Database, Cache, and Log Storage cards
          into a single capacity-oriented card. Each numeric value
          carries its unit (rows, MB, files, clients) so the reader
          doesn't have to guess what the digits represent. The "Tables"
          sub-section groups data-driven row names under one heading so
          each row doesn't have to repeat "Table:" as a prefix. */}
      {hasStorage && (
        <SettingsSection title="Storage">
          {about?.database && (
            <SettingsRowGrid>
              <SettingsRow
                name="Database size on disk"
                control={<span className={styles.mono}>{about.database.size}</span>}
              />
            </SettingsRowGrid>
          )}
          {about?.redis && (
            <SettingsRowGrid>
              <SettingsRow
                name="Cache memory in use"
                control={<span className={styles.mono}>{about.redis.used_memory_human}</span>}
              />
              <SettingsRow
                name="Cache peak memory"
                control={<span className={styles.mono}>{about.redis.peak_memory_human}</span>}
              />
              <SettingsRow
                name="Cache connected clients"
                control={
                  <span className={styles.mono}>
                    {formatNumber(about.redis.connected_clients)}
                    <span className={styles.unit}>clients</span>
                  </span>
                }
              />
            </SettingsRowGrid>
          )}
          {about?.logs && (
            <SettingsRowGrid>
              <SettingsRow
                name="Log size on disk"
                control={<span className={styles.mono}>{about.logs.total_size}</span>}
              />
              <SettingsRow
                name="Log files"
                control={
                  <span className={styles.mono}>
                    {formatNumber(about.logs.file_count)}
                    <span className={styles.unit}>files</span>
                  </span>
                }
              />
            </SettingsRowGrid>
          )}
          {about?.database?.top_tables && about.database.top_tables.length > 0 && (
            <>
              <div className={styles.groupLabel}>Tables</div>
              <SettingsRowGrid>
                {about.database.top_tables.map((t) => (
                  <SettingsRow
                    key={t.name}
                    name={t.name}
                    control={
                      <span className={styles.mono}>
                        {formatNumber(t.rows)}
                        <span className={styles.unit}>rows</span>
                      </span>
                    }
                  />
                ))}
              </SettingsRowGrid>
            </>
          )}
        </SettingsSection>
      )}
    </div>
  );
}
