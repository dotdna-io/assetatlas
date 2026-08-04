// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useCallback } from 'react';
import { assetsApi, useAddTag, useRemoveTag } from '@/api/assets';
import { Button } from '@/components/ui/Button';
import { ColorPicker } from '@/components/ui/ColorPicker/ColorPicker';
import { InlineStatus, successFor, useInlineStatus } from '@/components/ui/InlineStatus';
import styles from './edit.module.css';

interface TagTabProps {
  assetId: string;
  onSuccess: () => void;
}

interface Tag {
  id: string;
  tag_name: string;
  tag_color: string;
}

const DEFAULT_COLOR = '#3b82f6';

export function TagTab({ assetId, onSuccess }: TagTabProps) {
  const [status, setStatus] = useInlineStatus();
  const addTagMutation = useAddTag();
  const removeTagMutation = useRemoveTag();

  const [tags, setTags] = useState<Tag[]>([]);
  const [tagsLoading, setTagsLoading] = useState(false);
  const [newTagName, setNewTagName] = useState('');
  const [newTagColor, setNewTagColor] = useState(DEFAULT_COLOR);

  const loadTags = useCallback(async () => {
    setTagsLoading(true);
    try {
      const data = await assetsApi.getTags(assetId);
      const raw = data.tags ?? [];
      setTags(
        raw.map((t) => ({
          id: String(t.id ?? ''),
          tag_name: String(t.tag_name ?? t.name ?? ''),
          tag_color: String(t.tag_color ?? t.color ?? DEFAULT_COLOR),
        })),
      );
    } catch {
      setStatus({ kind: 'error', message: 'Failed to load tags' });
    } finally {
      setTagsLoading(false);
    }
  }, [assetId, setStatus]);

  useEffect(() => {
    loadTags();
  }, [loadTags]);

  const handleRemove = (tagId: string) => {
    removeTagMutation.mutate(
      { assetId, tagId },
      {
        onSuccess: () => {
          setTags((prev) => prev.filter((t) => t.id !== tagId));
          setStatus(null); // the tag leaves the list
          onSuccess();
        },
        onError: (err) => {
          setStatus({ kind: 'error', message: 'Failed to remove tag: ' + err.message });
        },
      },
    );
  };

  const handleAdd = () => {
    const name = newTagName.trim();
    if (!name) return;

    addTagMutation.mutate(
      { assetId, data: { tag_name: name, tag_color: newTagColor } },
      {
        onSuccess: () => {
          setNewTagName('');
          setNewTagColor(DEFAULT_COLOR);
          loadTags();
          setStatus({ kind: 'success', message: 'Tag added', action: 'add' });
          onSuccess();
        },
        onError: (err) => {
          setStatus({ kind: 'error', message: 'Failed to add tag: ' + err.message });
        },
      },
    );
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleAdd();
  };

  return (
    <div className={styles.tabContent}>
      <InlineStatus status={status} />
      {tagsLoading ? (
        <div className={styles.mergeEmptyText}>Loading tags...</div>
      ) : (
        <div className={styles.tagList}>
          {tags.length === 0 && (
            <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-size-sm)' }}>
              No tags assigned
            </span>
          )}
          {tags.map((tag) => (
            <span
              key={tag.id}
              className={styles.tagBadge}
              style={{
                color: tag.tag_color,
                borderColor: tag.tag_color,
                backgroundColor: tag.tag_color + '18',
              }}
            >
              {tag.tag_name}
              <button
                className={styles.tagRemove}
                onClick={() => handleRemove(tag.id)}
                aria-label={`Remove tag ${tag.tag_name}`}
                title="Remove"
              >
                &times;
              </button>
            </span>
          ))}
        </div>
      )}

      <div className={styles.formRow}>
        <label className={styles.formLabel}>Add Tag</label>
        <div className={styles.addTagRow}>
          <input
            type="text"
            className={styles.textInput}
            placeholder="Tag name..."
            value={newTagName}
            onChange={(e) => setNewTagName(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <Button
            variant="primary"
            size="sm"
            onClick={handleAdd}
            loading={addTagMutation.isPending}
            disabled={!newTagName.trim()}
            success={successFor(status, 'add')}
            successLabel="Added"
          >
            Add
          </Button>
        </div>
      </div>

      <div className={styles.formRow}>
        <label className={styles.formLabel}>Tag Color</label>
        <ColorPicker value={newTagColor} onChange={setNewTagColor} />
      </div>
    </div>
  );
}
