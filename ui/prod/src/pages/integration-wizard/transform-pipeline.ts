// SPDX-License-Identifier: AGPL-3.0-or-later
import { getNestedValue } from '@/pages/integrations/integration-utils';

export interface TransformOperation {
  id: string;
  type: 'field' | 'static' | 'current_timestamp' | 'trim' | 'uppercase' | 'lowercase'
      | 'regex_extract' | 'regex_replace' | 'coalesce';
  field?: string;
  value?: string;
  pattern?: string;
  group?: number;
  replacement?: string;
  fields?: string[];
  default?: string;
}

export interface Transformation {
  name: string;
  operations: TransformOperation[];
}

export interface PipelineStep {
  description: string;
  result: string;
}

export interface PipelineResult {
  steps: PipelineStep[];
  finalResult: string | null;
}

/**
 * Apply a transformation pipeline to a sample record.
 * Mirrors backend field_mapper._apply_transformation() exactly.
 */
export function applyTransformPipeline(
  operations: TransformOperation[],
  sampleRecord: Record<string, any>,
): PipelineResult {
  const steps: PipelineStep[] = [];
  let result = '';

  for (const op of operations) {
    switch (op.type) {
      case 'field': {
        const value = op.field ? getNestedValue(sampleRecord, op.field) : undefined;
        if (value != null) result += String(value);
        steps.push({
          description: `field "${op.field ?? ''}"`,
          result,
        });
        break;
      }
      case 'static': {
        if (op.value != null) result += String(op.value);
        steps.push({
          description: `static "${op.value ?? ''}"`,
          result,
        });
        break;
      }
      case 'current_timestamp': {
        // Stamps the collection time. Preview uses the browser clock; the real
        // value is produced server-side in field_mapper at ingest time.
        result += new Date().toISOString();
        steps.push({ description: 'current_timestamp', result });
        break;
      }
      case 'trim': {
        result = result.trim();
        steps.push({ description: 'trim', result });
        break;
      }
      case 'uppercase': {
        result = result.toUpperCase();
        steps.push({ description: 'uppercase', result });
        break;
      }
      case 'lowercase': {
        result = result.toLowerCase();
        steps.push({ description: 'lowercase', result });
        break;
      }
      case 'regex_extract': {
        const pattern = op.pattern;
        const group = op.group ?? 0;
        if (pattern) {
          try {
            const re = new RegExp(pattern);
            const match = re.exec(result);
            if (match && match[group] !== undefined) {
              result = match[group];
            } else {
              result = '';
            }
          } catch {
            result = '<error>';
          }
        }
        steps.push({
          description: `regex_extract /${op.pattern ?? ''}/ group=${group}`,
          result,
        });
        break;
      }
      case 'regex_replace': {
        const pattern = op.pattern;
        const replacement = op.replacement ?? '';
        if (pattern) {
          try {
            const re = new RegExp(pattern, 'g');
            result = result.replace(re, replacement);
          } catch {
            result = '<error>';
          }
        }
        steps.push({
          description: `regex_replace /${op.pattern ?? ''}/ → "${replacement}"`,
          result,
        });
        break;
      }
      case 'coalesce': {
        const fields = op.fields ?? [];
        const defaultValue = op.default ?? '';
        let found: string | null = null;
        for (const fieldPath of fields) {
          const value = getNestedValue(sampleRecord, fieldPath);
          if (value != null && String(value).trim() !== '') {
            found = String(value);
            break;
          }
        }
        result += found ?? defaultValue;
        steps.push({
          description: `coalesce [${fields.join(', ')}]${defaultValue ? ` default="${defaultValue}"` : ''}`,
          result,
        });
        break;
      }
    }
  }

  const finalResult = result.trim() ? result : null;
  return { steps, finalResult };
}

export function generateOpId(): string {
  return `op_${crypto.randomUUID()}`;
}
