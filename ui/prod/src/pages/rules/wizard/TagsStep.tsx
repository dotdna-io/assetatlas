// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect } from 'react';
import { Badge } from '@/components/ui/Badge';
import { ColorPicker } from '@/components/ui/ColorPicker';
import type { TagRuleTags } from '@/api/tag-rules';
import styles from '../rules.module.css';

const DEFAULT_TAG_COLOR = '#64748b';

// Saving the rule doesn't tag anything — the pipeline does, on its next run.
// This is the step where people expect the tag to take effect, so say it here
// rather than leaving them to find an empty tag on the assets afterwards.
const PIPELINE_NOTE =
  'Tags are written onto matching assets on the next pipeline run — not the moment you save.';

interface TagsStepProps {
  ruleType: 'compliance' | 'classification';
  tags: TagRuleTags;
  onTagsChange: (tags: TagRuleTags) => void;
}

export function TagsStep({
  ruleType,
  tags,
  onTagsChange,
}: TagsStepProps) {
  // Classification rules render a Badge + ColorPicker that fall back to a
  // default swatch (with check mark) when `tags.color` is missing — but the
  // parent state stays undefined, and submit fails the backend's hex check.
  // Sync the displayed default into state so what the user sees matches what
  // gets sent.
  useEffect(() => {
    if (ruleType === 'classification' && !tags.color) {
      onTagsChange({ ...tags, color: DEFAULT_TAG_COLOR });
    }
  }, [ruleType, tags, onTagsChange]);

  if (ruleType === 'compliance') {
    return (
      <div>
        <p className={styles.formHint}>{PIPELINE_NOTE}</p>

        <div className={styles.formGroup}>
          <label className={styles.formLabel} htmlFor="pass-tag">
            Pass Tag
          </label>
          <input
            id="pass-tag"
            className={styles.formInput}
            type="text"
            value={tags.pass ?? ''}
            onChange={(e) => onTagsChange({ ...tags, pass: e.target.value || undefined })}
            placeholder="e.g. compliant"
          />
          {tags.pass && (
            <div className={styles.tagPreview}>
              <span className={styles.tagPreviewLabel}>Preview:</span>
              <Badge variant="success">{tags.pass}</Badge>
            </div>
          )}
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel} htmlFor="fail-tag">
            Fail Tag
          </label>
          <input
            id="fail-tag"
            className={styles.formInput}
            type="text"
            value={tags.fail ?? ''}
            onChange={(e) => onTagsChange({ ...tags, fail: e.target.value || undefined })}
            placeholder="e.g. non-compliant"
          />
          {tags.fail && (
            <div className={styles.tagPreview}>
              <span className={styles.tagPreviewLabel}>Preview:</span>
              <Badge variant="danger">{tags.fail}</Badge>
            </div>
          )}
        </div>

      </div>
    );
  }

  // classification
  return (
    <div>
      <p className={styles.formHint}>{PIPELINE_NOTE}</p>

      <div className={styles.formGroup}>
        <label className={styles.formLabel} htmlFor="tag-label">
          Tag Label
        </label>
        <input
          id="tag-label"
          className={styles.formInput}
          type="text"
          value={tags.label ?? ''}
          onChange={(e) => onTagsChange({ ...tags, label: e.target.value || undefined })}
          placeholder="e.g. production"
        />
        {tags.label && (
          <div className={styles.tagPreview}>
            <span className={styles.tagPreviewLabel}>Preview:</span>
            <Badge color={tags.color || DEFAULT_TAG_COLOR}>{tags.label}</Badge>
          </div>
        )}
      </div>

      <div className={styles.formGroup}>
        <label className={styles.formLabel}>Tag Color</label>
        <ColorPicker
          value={tags.color ?? DEFAULT_TAG_COLOR}
          onChange={(color) => onTagsChange({ ...tags, color })}
        />
      </div>
    </div>
  );
}
