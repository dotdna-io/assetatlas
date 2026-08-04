// SPDX-License-Identifier: AGPL-3.0-or-later
import styles from './WelcomePanel.module.css';

const STARTER_PROMPTS = [
  'How many assets do I have, grouped by category?',
  'Show me the top 5 active alerts.',
  'Are there any failing integrations right now?',
] as const;

interface WelcomePanelProps {
  onPrompt: (text: string) => void;
  disabled?: boolean;
}

export function WelcomePanel({ onPrompt, disabled }: WelcomePanelProps) {
  return (
    <div className={styles.wrap}>
      <h3 className={styles.title}>Ask about your assets</h3>
      <p className={styles.subtitle}>
        I can search inventory, summarise alerts, and surface pipeline health.
        Try one of these to get started:
      </p>
      <div className={styles.prompts}>
        {STARTER_PROMPTS.map((p) => (
          <button
            key={p}
            type="button"
            className={styles.prompt}
            onClick={() => onPrompt(p)}
            disabled={disabled}
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}
