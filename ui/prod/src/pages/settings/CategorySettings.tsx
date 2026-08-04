// SPDX-License-Identifier: AGPL-3.0-or-later
// ui/prod/src/pages/settings/CategorySettings.tsx
import { useState } from 'react';
import {
  useCategorySettings,
  useSettingsSchema,
  useUpdateSetting,
  useResetSetting,
} from '@/api/settings';
import {
  SettingsSection,
  SettingsRowGrid,
  SettingsRow,
  SettingsFullWidthBlock,
} from '@/components/settings';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import { Button } from '@/components/ui/Button';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { joinLabels } from '@/utils/formatters';
import styles from './CategorySettings.module.css';

/**
 * A category's master switch, promoted out of the settings grid and rendered
 * above the card so the control that decides whether the whole feature is
 * live doesn't read as one row among fifteen (#1149). Same placement and
 * weight as the Email tab's "Enable outbound email".
 *
 * The key must be `hidden` in settings_schema.py, or the grid draws it a
 * second time. It stays part of the draft form: flipping it marks the tab
 * dirty and it commits with the same Save as every other row — never on
 * click, which would half-commit a form whose other fields are still dirty.
 */
export interface MasterToggleConfig {
  // Setting key within the category — 'enabled' for every current caller.
  settingKey: string;
  label: string;
  // Keys that must ALSO hold a value before the feature is actually live.
  // The service-side `is_enabled()` is the flag AND a config-completeness
  // check, so without this the switch can read "on" while the feature is
  // inert and nothing on screen explains why.
  requiredKeys?: Array<{ key: string; label: string }>;
  // Effective-state line when the switch is off / on-and-complete. The
  // on-but-incomplete case is generated from `requiredKeys`.
  offNote?: string;
  onNote?: string;
}

interface CategorySettingsProps {
  category: string;
  // 'grid' (default) = side-by-side 2-column layout used by most tabs.
  // 'stack' = full-width single column. Pick this when individual rows
  // need the full width to display long values (URLs, paths, etc.).
  layout?: 'grid' | 'stack';
  // When true, hide the inline description and surface it as a hover
  // tooltip on the setting name. Use when descriptions are long enough
  // that inline rendering would crowd the row.
  descriptionAsTooltip?: boolean;
  // Per-key fallback values used to pre-populate string inputs when the
  // saved value is empty. Today's only consumer is the Security tab,
  // which fetches the env-var-resolved public_base_url so the operator
  // sees the install's bootstrap default in the input box rather than
  // a blank field.
  effectiveValues?: Record<string, string>;
  // Optional content rendered at the top of the card, before any rows.
  // Lets a tab fold a short description / help block inside the same
  // glossy section instead of stacking a second card above it.
  intro?: React.ReactNode;
  // Override the section title (default comes from CATEGORY_TITLES or the
  // category key). Pair with `descriptive` when the override is a full
  // sentence rather than a chip-style label.
  title?: string;
  descriptive?: boolean;
  // Extra actions rendered on the left of the card's bottom action row,
  // opposite the Save button. Used by tabs that own a per-card secondary
  // action like "Test connection".
  leftActions?: React.ReactNode;
  // Promote one boolean key into a master switch above the card. See
  // MasterToggleConfig.
  masterToggle?: MasterToggleConfig;
}

interface SettingDef {
  type: string;
  default: any;
  description: string;
  min?: number;
  max?: number;
  options?: string[];
  requires_restart?: boolean;
  // When true, the setting stays in the schema (so settings_manager.set still
  // validates writes) but a bespoke tab owns its UI — skip it in this generic
  // renderer. E.g. chat.active_provider, owned by the provider Configuration tab.
  hidden?: boolean;
}

const CATEGORY_TITLES: Record<string, string> = {
  retention: 'Retention',
  security: 'Security',
  logging: 'Logging',
  auth: 'Password Policy',
};

/**
 * Per-key display-name overrides — used when the auto-generated
 * "snake_case → Title Case" label reads awkwardly. The underlying setting
 * key stays unchanged (so the API + schema stay stable).
 */
const KEY_LABELS: Record<string, string> = {
  public_base_url: 'Base URL',
};

/**
 * Pulls a "Example: {...}" code block out of a setting description so the
 * inline text reads cleanly and the JSON itself can be rendered as a code
 * block outside the textarea. Brace-balanced extraction so nested keys
 * don't get clipped early.
 */
function extractExample(desc: string): { description: string; example: string | null } {
  const idx = desc.indexOf('Example:');
  if (idx === -1) return { description: desc, example: null };

  const start = desc.indexOf('{', idx);
  if (start === -1) return { description: desc, example: null };

  let depth = 0;
  let end = -1;
  for (let i = start; i < desc.length; i++) {
    const c = desc[i];
    if (c === '{') depth++;
    else if (c === '}') {
      depth--;
      if (depth === 0) { end = i + 1; break; }
    }
  }
  if (end === -1) return { description: desc, example: null };

  const example = desc.slice(start, end);
  const before = desc.slice(0, idx).trim().replace(/\.$/, '');
  const after = desc.slice(end).trim().replace(/^\.\s*/, '').trim();
  const description = [before, after].filter(Boolean).join(' ').trim();
  return { description, example };
}

function prettyJson(raw: string): string {
  try { return JSON.stringify(JSON.parse(raw), null, 2); } catch { return raw; }
}

export function CategorySettings({
  category,
  layout = 'grid',
  descriptionAsTooltip = false,
  effectiveValues,
  intro,
  title,
  descriptive,
  leftActions,
  masterToggle,
}: CategorySettingsProps) {
  const { data: catData, isLoading: catLoading } = useCategorySettings(category);
  const { data: schemaData, isLoading: schemaLoading } = useSettingsSchema();
  const updateSetting = useUpdateSetting();
  const resetSetting = useResetSetting();
  // Errors only — success is deliberately silent (the form re-renders with the
  // saved values, so the user sees the result without a banner).
  const [status, setStatus] = useInlineStatus();
  const [edits, setEdits] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);

  if (catLoading || schemaLoading) {
    return <LoadingSpinner />;
  }

  const settings = catData?.settings ?? {};
  const schema: Record<string, SettingDef> = schemaData?.schema?.[category] ?? {};

  // Skip settings a bespoke tab owns (def.hidden) so they don't render here.
  const visibleSchema = Object.entries(schema).filter(([, def]) => !def.hidden);
  const entries = visibleSchema.map(([key, def]) => {
    const saved = rawSavedOf(key);
    const isEmptyString = def.type === 'string' && (saved === '' || saved == null);
    const effective = effectiveValues?.[key];
    const value =
      saved != null && !isEmptyString
        ? saved
        : effective != null && effective !== ''
        ? effective
        : def.default;
    return { key, def, value };
  });

  const simpleEntries = entries.filter((e) => e.def.type !== 'json');
  const jsonEntries = entries.filter((e) => e.def.type === 'json');

  const isDirty = Object.keys(edits).length > 0;

  /**
   * The stored value for a key, unwrapped from the `{ value, default, … }`
   * envelope the settings API returns. A row whose `value` is null must NOT
   * collapse to the envelope object itself — `!!{…}` is `true` and
   * `String({…})` is "[object Object]", so a null-valued flag would read as
   * "on" and a null-valued required field as "configured".
   */
  function rawSavedOf(key: string) {
    const entry = settings[key];
    return entry && typeof entry === 'object' && 'value' in entry ? entry.value : entry;
  }

  /** The stored value, falling back to the schema default. */
  function savedValueOf(key: string) {
    return rawSavedOf(key) ?? schema[key]?.default;
  }

  /**
   * What Save would write for a key: the pending edit, else the saved value,
   * else the schema default. Used by the master toggle so its state and its
   * effective-state note both describe the draft — the alternative (switch
   * shows the draft, note describes the server) reads as a contradiction the
   * moment the form is dirty.
   */
  function draftValueOf(key: string) {
    if (edits[key] !== undefined) return edits[key];
    return savedValueOf(key);
  }

  // Mirrors the service-side is_enabled(): the flag is necessary but not
  // sufficient, so an "on" switch with a blank required field is reported as
  // enabled-but-inert rather than simply "on".
  const masterEnabled = masterToggle ? !!draftValueOf(masterToggle.settingKey) : false;
  const masterMissing = (masterToggle?.requiredKeys ?? []).filter(
    ({ key }) => !String(draftValueOf(key) ?? '').trim(),
  );
  // True while any key the note is derived from carries an unsaved edit — the
  // switch itself or one of the required fields below it. Watching only the
  // switch would leave the same hole open one field over: typing into a blank
  // Server URL flips the note to "live" with nothing saved.
  const masterPending = masterToggle
    ? [masterToggle.settingKey, ...(masterToggle.requiredKeys ?? []).map((r) => r.key)].some(
        (key) => edits[key] !== undefined,
      )
    : false;

  function masterNote(): { text: string; warn: boolean } | null {
    if (!masterToggle) return null;
    let text = '';
    let warn = false;
    if (!masterEnabled) {
      text = masterToggle.offNote ?? '';
    } else if (masterMissing.length > 0) {
      text = `Enabled, but incomplete — ${joinLabels(
        masterMissing.map((m) => m.label),
      )} not configured.`;
      warn = true;
    } else {
      text = masterToggle.onNote ?? '';
    }
    // The note tracks the draft, so while the switch differs from what is
    // stored it describes something that has not happened yet. Say so, or an
    // admin flips the switch, reads a present-tense confirmation, and leaves
    // without reaching the Save button further down a 15-row card.
    if (masterPending) {
      text = `${text} Not applied until you press Save.`.trim();
    }
    return text ? { text, warn } : null;
  }

  function clearEdit(key: string) {
    setEdits((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }

  function handleReset(key: string) {
    resetSetting.mutate(
      { category, key, reason: 'Reset from Settings UI' },
      {
        onSuccess: () => clearEdit(key),
        onError: (err) =>
          setStatus({ kind: 'error', message: err.message || 'Failed to reset' }),
      },
    );
  }

  /**
   * Validate all draft edits, then batch-update via Promise.allSettled so a
   * single bad field doesn't take down the others. Any rejection becomes the
   * inline error next to the Save button; success clears the draft state and
   * lets the cache invalidation re-render the form.
   */
  async function handleSaveAll() {
    if (!isDirty) return;

    const validated: Array<[string, any]> = [];
    const validationErrors: string[] = [];

    for (const [key, raw] of Object.entries(edits)) {
      const def = schema[key];
      if (!def) continue;
      let parsed: any = raw;
      if (def.type === 'integer') {
        parsed = typeof raw === 'number' ? raw : parseInt(String(raw), 10);
        if (Number.isNaN(parsed)) {
          validationErrors.push(`${key}: invalid integer`);
          continue;
        }
      } else if (def.type === 'float') {
        parsed = typeof raw === 'number' ? raw : parseFloat(String(raw));
        if (Number.isNaN(parsed)) {
          validationErrors.push(`${key}: invalid number`);
          continue;
        }
      } else if (def.type === 'json') {
        if (typeof raw === 'string') {
          try {
            parsed = JSON.parse(raw);
          } catch {
            validationErrors.push(`${key}: invalid JSON`);
            continue;
          }
        }
      }
      validated.push([key, parsed]);
    }

    if (validationErrors.length > 0) {
      setStatus({ kind: 'error', message: validationErrors.join('; ') });
      return;
    }

    setSaving(true);
    try {
      const results = await Promise.allSettled(
        validated.map(([key, value]) =>
          updateSetting.mutateAsync({
            category,
            key,
            data: { value, reason: 'Updated from Settings UI' },
          }),
        ),
      );
      const failures = results
        .map((r, i) =>
          r.status === 'rejected'
            ? `${validated[i][0]}: ${(r.reason as Error)?.message || 'failed'}`
            : null,
        )
        .filter((x): x is string => Boolean(x));
      if (failures.length > 0) {
        setStatus({ kind: 'error', message: failures.join('; ') });
        return;
      }
      setEdits({});
    } finally {
      setSaving(false);
    }
  }

  function renderControl(key: string, def: SettingDef, currentValue: any) {
    const editValue = edits[key];
    const draftValue = editValue !== undefined ? editValue : currentValue;

    switch (def.type) {
      case 'boolean':
        return (
          <ToggleSwitch
            checked={!!draftValue}
            onChange={(checked) => setEdits((prev) => ({ ...prev, [key]: checked }))}
          />
        );

      case 'integer':
      case 'float':
        return (
          <div className={styles.inputGroup}>
            <input
              type="number"
              className={styles.numberInput}
              value={draftValue ?? ''}
              min={def.min}
              max={def.max}
              step={def.type === 'float' ? 0.01 : 1}
              onChange={(e) =>
                setEdits((prev) => ({ ...prev, [key]: e.target.value }))
              }
            />
          </div>
        );

      case 'string':
        if (def.options && def.options.length > 0) {
          return (
            <select
              className={styles.selectInput}
              value={draftValue ?? ''}
              onChange={(e) =>
                setEdits((prev) => ({ ...prev, [key]: e.target.value }))
              }
            >
              {def.options.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          );
        }
        return (
          <div className={styles.inputGroup}>
            <input
              type="text"
              className={`${styles.textInput} ${
                layout === 'stack' ? styles.textInputWide : ''
              }`}
              value={draftValue ?? ''}
              onChange={(e) =>
                setEdits((prev) => ({ ...prev, [key]: e.target.value }))
              }
            />
          </div>
        );

      default:
        return <span>{String(currentValue)}</span>;
    }
  }

  function formatDisplayName(key: string): string {
    if (KEY_LABELS[key]) return KEY_LABELS[key];
    return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function metaFor(def: SettingDef): string {
    const parts: string[] = [];
    // Surface a "Default: X" hint only when there's something useful to
    // show. For string fields with an empty default, the line would just
    // read "Default: " and adds noise (e.g. Public Base URL).
    const hasMeaningfulDefault =
      def.default !== '' && def.default !== null && def.default !== undefined;
    if (hasMeaningfulDefault) {
      const rendered =
        typeof def.default === 'object'
          ? JSON.stringify(def.default)
          : String(def.default);
      parts.push(`Default: ${rendered}`);
    }
    // Min/Max only apply to numeric types; the schema sends `null` for
    // unset bounds, so guard with `!= null` (catches both null and undefined).
    if (def.type === 'integer' || def.type === 'float') {
      if (def.min != null) parts.push(`Min: ${def.min}`);
      if (def.max != null) parts.push(`Max: ${def.max}`);
    }
    if (def.requires_restart) parts.push('Requires restart');
    return parts.join(' | ');
  }

  function renderJsonControl(key: string, def: SettingDef, currentValue: any) {
    const editValue = edits[key];
    const draftValue = editValue !== undefined ? editValue : currentValue;
    const placeholder =
      typeof def.default === 'object'
        ? JSON.stringify(def.default)
        : def.default != null
        ? String(def.default)
        : '';
    return (
      <div className={styles.jsonInputGroup}>
        <textarea
          className={styles.jsonInput}
          placeholder={placeholder}
          value={
            draftValue !== undefined
              ? typeof draftValue === 'string'
                ? draftValue
                : JSON.stringify(draftValue, null, 2)
              : ''
          }
          onChange={(e) =>
            setEdits((prev) => ({ ...prev, [key]: e.target.value }))
          }
        />
      </div>
    );
  }

  const sectionTitle = title ?? CATEGORY_TITLES[category] ?? formatDisplayName(category);
  const note = masterNote();

  return (
    <>
      {masterToggle && (
        <div className={styles.masterToggle}>
          <ToggleSwitch
            checked={masterEnabled}
            ariaLabel={masterToggle.label}
            onChange={(checked) =>
              setEdits((prev) => ({ ...prev, [masterToggle.settingKey]: checked }))
            }
          />
          <span className={styles.masterToggleText}>
            <span className={styles.masterToggleLabel}>{masterToggle.label}</span>
            {note && (
              <span
                className={`${styles.masterToggleNote} ${
                  note.warn ? styles.masterToggleWarn : ''
                }`}
              >
                {note.text}
              </span>
            )}
          </span>
        </div>
      )}
      <SettingsSection title={sectionTitle} descriptive={descriptive}>
        {intro && <div className={styles.intro}>{intro}</div>}
        {simpleEntries.length > 0 && (
          <SettingsRowGrid layout={layout}>
            {simpleEntries.map(({ key, def, value }) => (
              <SettingsRow
                key={key}
                name={formatDisplayName(key)}
                description={def.description}
                descriptionAsTooltip={descriptionAsTooltip}
                meta={metaFor(def)}
                control={renderControl(key, def, value)}
                onReset={() => handleReset(key)}
                resetLabel={`Reset ${formatDisplayName(key)} to default`}
              />
            ))}
          </SettingsRowGrid>
        )}
        {jsonEntries.map(({ key, def, value }) => {
          const { description, example } = extractExample(def.description);
          return (
            <SettingsFullWidthBlock key={key}>
              <div className={styles.jsonRow}>
                <div className={styles.jsonInfo}>
                  <div className={styles.jsonName}>{formatDisplayName(key)}</div>
                  <div className={styles.jsonDescription}>{description}</div>
                  <div className={styles.jsonMeta}>{metaFor(def)}</div>
                  {example && (
                    <div className={styles.jsonExample}>
                      <span className={styles.jsonExampleLabel}>Example</span>
                      <pre className={styles.jsonExampleCode}>{prettyJson(example)}</pre>
                    </div>
                  )}
                </div>
                {renderJsonControl(key, def, value)}
                <button
                  type="button"
                  className={styles.jsonReset}
                  onClick={() => handleReset(key)}
                  aria-label={`Reset ${formatDisplayName(key)} to default`}
                >
                  Reset
                </button>
              </div>
            </SettingsFullWidthBlock>
          );
        })}

        <div className={styles.actions}>
          <div className={styles.actionsLeft}>{leftActions}</div>
          <div className={styles.actionsRight}>
            {status?.kind === 'error' && <InlineStatus status={status} align="inline" />}
            <Button
              variant={isDirty ? 'primary' : 'secondary'}
              onClick={handleSaveAll}
              loading={saving}
              disabled={!isDirty}
            >
              Save
            </Button>
          </div>
        </div>
      </SettingsSection>
    </>
  );
}
