// SPDX-License-Identifier: AGPL-3.0-or-later
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/Button';
import { assetUrl } from '@/basePath';
import styles from './dashboard.module.css';

/**
 * First-run state for the homepage: shown only while no integration has been
 * configured at all.
 *
 * It replaces the dashboard rather than sitting above them. With no
 * integrations there is nothing to summarise — every card reads 0 / "No
 * alerts" / "No data sources" — so the usual layout is worse than useless: it
 * looks broken and buries the one action that matters. Nothing here is
 * dismissible on purpose; the panel disappears by itself the moment an
 * integration exists, so there is no dismissal state to persist.
 */
export function FirstRunPanel() {
  const navigate = useNavigate();

  return (
    <div className={styles.firstRun}>
      <div className={styles.firstRunMasthead}>
        <img
          className={styles.firstRunMark}
          src={assetUrl('/assetatlas_logo.png')}
          alt=""
          aria-hidden="true"
        />
        <span className={styles.firstRunEyebrow}>First run</span>
      </div>

      <h2 className={styles.firstRunTitle}>Your inventory starts with one connection</h2>
      <p className={styles.firstRunLede}>
        AssetAtlas holds no data of its own. Connect the systems and tools already in use across
        your environment and it consolidates their records into a single inventory.
      </p>

      <ol className={styles.firstRunSteps}>
        <li className={styles.firstRunStep}>
          <span className={styles.firstRunStepNum}>Step 1</span>
          <h3 className={styles.firstRunStepTitle}>Connect a data source</h3>
          <p className={styles.firstRunStepBody}>
            Connect your systems and tools through the integrations available in the Hub.
          </p>
        </li>
        <li className={styles.firstRunStep}>
          <span className={styles.firstRunStepNum}>Step 2</span>
          <h3 className={styles.firstRunStepTitle}>Let the pipeline run</h3>
          <p className={styles.firstRunStepBody}>
            The data pipeline generates your inventory from the connected sources — deduplicated,
            enriched, and refreshed on every run.
          </p>
        </li>
        <li className={styles.firstRunStep}>
          <span className={styles.firstRunStepNum}>Step 3</span>
          <h3 className={styles.firstRunStepTitle}>Work the inventory</h3>
          <p className={styles.firstRunStepBody}>
            Explore the inventory or chat with it, apply rules, investigate anomalies, and build
            reports.
          </p>
        </li>
      </ol>

      <div className={styles.firstRunActions}>
        <Button size="lg" onClick={() => navigate('/integrations')}>
          Connect a data source
        </Button>
        <Button variant="ghost" size="lg" onClick={() => navigate('/help/integrations')}>
          How connecting works
        </Button>
      </div>
    </div>
  );
}
