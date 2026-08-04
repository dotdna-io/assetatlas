// SPDX-License-Identifier: AGPL-3.0-or-later
import styles from '../rules.module.css';

interface NameStepProps {
  name: string;
  onNameChange: (name: string) => void;
  description: string;
  onDescriptionChange: (description: string) => void;
}

export function NameStep({ name, onNameChange, description, onDescriptionChange }: NameStepProps) {
  return (
    <div>
      <div className={styles.formGroup}>
        <label className={styles.formLabel} htmlFor="rule-name">
          Rule Name
        </label>
        <input
          id="rule-name"
          className={styles.formInput}
          type="text"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          placeholder="Enter rule name"
          autoFocus
        />
      </div>

      <div className={styles.formGroup}>
        <label className={styles.formLabel} htmlFor="rule-description">
          Description{' '}
          <span className={styles.formLabelOptional}>(optional)</span>
        </label>
        <textarea
          id="rule-description"
          className={styles.formTextarea}
          value={description}
          onChange={(e) => onDescriptionChange(e.target.value)}
          placeholder="Describe what this rule does"
        />
      </div>
    </div>
  );
}
