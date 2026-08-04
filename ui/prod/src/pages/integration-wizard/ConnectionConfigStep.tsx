// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useRef, useEffect, useMemo } from 'react';
import { useAvailableConnectors } from '@/api/integrations';
import { getConnectorDisplayName, isSensitiveField } from '@/pages/integrations/integration-utils';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch/ToggleSwitch';
import { EyeIcon } from '@/components/ui/EyeIcon';
import type { WizardFormData, WizardMode } from './wizard-state';
import styles from './ConnectionConfigStep.module.css';

interface ConnectionConfigStepProps {
  formData: WizardFormData;
  onUpdate: (partial: Partial<WizardFormData>) => void;
  mode: WizardMode;
}

interface SelectOption {
  label: string;
  value: string;
}

interface FieldDef {
  key: string;
  label: string;
  type: 'text' | 'password' | 'number' | 'url' | 'select' | 'textarea' | 'boolean';
  required: boolean;
  description?: string;
  options?: SelectOption[];
  defaultValue?: any;
  placeholder?: string;
  min?: number;
  max?: number;
  depends_on?: { field: string; value: any };
}


function schemaToFields(schema: Record<string, any>): FieldDef[] {
  const hasProperties = schema?.properties || schema?.fields;
  const properties = hasProperties ? (schema.properties || schema.fields) : schema;
  const requiredList = schema?.required;

  return Object.entries(properties).map(([key, prop]: [string, any]) => {
    if (typeof prop !== 'object' || prop === null) return null;

    let type: FieldDef['type'] = 'text';
    if (prop.type === 'boolean') type = 'boolean';
    else if (prop.type === 'number' || prop.type === 'integer') type = 'number';
    else if (prop.type === 'select' || prop.enum) type = 'select';
    else if (prop.type === 'password' || prop.sensitive) type = 'password';
    else if (prop.type === 'url' || prop.format === 'url' || prop.format === 'uri') type = 'url';
    else if (prop.format === 'password' || isSensitiveField(key)) type = 'password';
    else if (prop.format === 'textarea' || prop.format === 'multiline') type = 'textarea';

    const isRequired = prop.required === true || (Array.isArray(requiredList) && requiredList.includes(key));

    // Normalize enum options to { label, value } objects
    let options: SelectOption[] | undefined;
    if (prop.enum) {
      options = prop.enum.map((opt: any) => {
        if (typeof opt === 'object' && opt.label && opt.value !== undefined) {
          return { label: opt.label, value: String(opt.value) };
        }
        return { label: String(opt), value: String(opt) };
      });
    } else if (prop.options) {
      options = prop.options.map((opt: any) => {
        if (typeof opt === 'object' && opt.label && opt.value !== undefined) {
          return { label: opt.label, value: String(opt.value) };
        }
        return { label: String(opt), value: String(opt) };
      });
    }

    return {
      key,
      label: prop.label || prop.title || key.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase()),
      type,
      required: isRequired,
      description: prop.description,
      options,
      defaultValue: prop.default,
      placeholder: prop.placeholder,
      min: prop.validation?.min ?? prop.minimum,
      max: prop.validation?.max ?? prop.maximum,
      depends_on: prop.depends_on,
    };
  }).filter(Boolean) as FieldDef[];
}

export function ConnectionConfigStep({ formData, onUpdate, mode }: ConnectionConfigStepProps) {
  const integrationType = formData.integration_type || '';
  const isManualUpload = integrationType === 'manual_upload';

  // Get config_schema from the connector object (from /api/integrations/available).
  // When source_method is not 'direct', the config belongs to the transport connector
  // (e.g. Splunk), so look that up instead.
  const schemaType =
    formData.source_method && formData.source_method !== 'direct'
      ? formData.source_method
      : integrationType;
  const { data: connectorsData } = useAvailableConnectors();
  const connector = connectorsData?.connectors?.find(
    (c: any) => c.integration_type === schemaType,
  );

  const [visiblePasswords, setVisiblePasswords] = useState<Record<string, boolean>>({});
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const togglePassword = (key: string) => {
    setVisiblePasswords((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const fields = useMemo<FieldDef[]>(() => {
    if (isManualUpload || !connector?.config_schema) return [];
    return schemaToFields(connector.config_schema);
  }, [isManualUpload, connector]);

  // Pre-fill default values on mount (create mode only, don't override existing config)
  const defaultsApplied = useRef(false);
  useEffect(() => {
    if (defaultsApplied.current || mode === 'edit' || fields.length === 0) return;
    defaultsApplied.current = true;
    const defaults: Record<string, any> = {};
    for (const field of fields) {
      if (field.defaultValue !== undefined && formData.config[field.key] === undefined) {
        defaults[field.key] = field.defaultValue;
      }
    }
    if (Object.keys(defaults).length > 0) {
      onUpdate({ config: { ...formData.config, ...defaults } });
    }
  }, [fields]);

  const handleConfigChange = (key: string, value: any) => {
    onUpdate({ config: { ...formData.config, [key]: value } });
  };

  const handleFileChange = (file: File | null) => {
    if (!file) return;
    // Drop any previously-read content along with the new pick. Keeping it
    // would let "choose a different file, save without re-processing" import
    // the OLD file under the NEW name — silently wrong, and indistinguishable
    // from success. The next step reads and repopulates these (#1133).
    const { file_data: _drop, file_format: _drop2, file_name: _drop3, ...rest } =
      formData.config;
    onUpdate({ config: { ...rest, _file: file, _fileName: file.name } });
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0] || null;
    handleFileChange(file);
  };

  const renderField = (field: FieldDef) => {
    if (field.depends_on) {
      if (formData.config[field.depends_on.field] !== field.depends_on.value) {
        return null;
      }
    }

    const value = formData.config[field.key];

    switch (field.type) {
      case 'select':
        return (
          <select
            id={`config-${field.key}`}
            className={styles.fieldInput}
            value={value ?? ''}
            onChange={(e) => handleConfigChange(field.key, e.target.value)}
          >
            <option value="">Select...</option>
            {field.options?.map(opt => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        );
      case 'number':
        return (
          <input
            id={`config-${field.key}`}
            type="number"
            className={styles.fieldInput}
            value={value ?? ''}
            min={field.min}
            max={field.max}
            placeholder={field.placeholder || (field.defaultValue != null ? String(field.defaultValue) : '')}
            onChange={(e) => handleConfigChange(field.key, e.target.value ? Number(e.target.value) : '')}
          />
        );
      case 'textarea':
        return (
          <textarea
            id={`config-${field.key}`}
            className={styles.fieldTextarea}
            value={value ?? ''}
            rows={4}
            onChange={(e) => handleConfigChange(field.key, e.target.value)}
          />
        );
      case 'boolean':
        return (
          <ToggleSwitch checked={!!value} onChange={(checked) => handleConfigChange(field.key, checked)} />
        );
      case 'url':
        return (
          <input
            id={`config-${field.key}`}
            type="url"
            className={styles.fieldInput}
            value={value ?? ''}
            placeholder={field.placeholder || 'https://...'}
            onChange={(e) => handleConfigChange(field.key, e.target.value)}
          />
        );
      case 'password':
        return (
          <div className={styles.passwordField}>
            <input
              id={`config-${field.key}`}
              type={visiblePasswords[field.key] ? 'text' : 'password'}
              className={styles.fieldInput}
              value={value as string || ''}
              onChange={(e) => handleConfigChange(field.key, e.target.value)}
              placeholder={mode === 'edit' ? 'Leave blank to keep current' : undefined}
              autoComplete="off"
            />
            <button
              type="button"
              className={styles.passwordToggle}
              onClick={() => togglePassword(field.key)}
              aria-label={visiblePasswords[field.key] ? 'Hide password' : 'Show password'}
            >
              <EyeIcon size={14} open={!visiblePasswords[field.key]} />
            </button>
          </div>
        );
      default:
        return (
          <input
            id={`config-${field.key}`}
            type="text"
            className={styles.fieldInput}
            value={value as string || ''}
            placeholder={field.placeholder || ''}
            onChange={(e) => handleConfigChange(field.key, e.target.value)}
          />
        );
    }
  };

  return (
    <div className={styles.form}>
      {isManualUpload ? (
        <div className={styles.field}>
          <label className={styles.fieldLabel}>Upload File</label>
          <div
            className={[styles.fileUpload, dragOver ? styles.fileUploadOver : ''].filter(Boolean).join(' ')}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') fileInputRef.current?.click(); }}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,.xlsx,.json"
              className={styles.fileInputHidden}
              onChange={(e) => handleFileChange(e.target.files?.[0] || null)}
            />
            {(formData.config._fileName ?? formData.config.file_name) ? (
              <span className={styles.fileName}>
                {(formData.config._fileName ?? formData.config.file_name) as string}
              </span>
            ) : (
              <>
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{color: 'var(--text-tertiary)'}}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                <span>Drag and drop a file here, or click to browse</span>
                <span className={styles.fileUploadHint}>Supported formats: CSV, XLSX, JSON</span>
              </>
            )}
          </div>
        </div>
      ) : (
        fields.map((field) => {
          const rendered = renderField(field);
          if (rendered === null) return null;
          return (
            <div key={field.key} className={styles.field}>
              <label className={styles.fieldLabel} htmlFor={`config-${field.key}`}>
                {field.label}
                {field.required && <span className={styles.required}> *</span>}
              </label>
              {rendered}
              {field.description && (
                <div className={styles.fieldHelp}>{field.description}</div>
              )}
            </div>
          );
        })
      )}

      {!isManualUpload && fields.length === 0 && (
        <p className={styles.noFields}>No configuration fields required for this connector type.</p>
      )}
    </div>
  );
}
