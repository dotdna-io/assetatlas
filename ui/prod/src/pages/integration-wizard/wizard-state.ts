// SPDX-License-Identifier: AGPL-3.0-or-later
export type WizardMode = 'create' | 'edit' | 'clone';
export type WizardStep = 'type-select' | 1 | 2 | 3 | 4;
type ValidatableStep = 1 | 2 | 3;

import type { Transformation, TransformOperation } from './transform-pipeline';
import { generateOpId } from './transform-pipeline';
import { FRAMEWORK_FIELDS } from '@/pages/integrations/integration-utils';

const FRAMEWORK_FIELD_KEYS = new Set<string>(FRAMEWORK_FIELDS.map((f) => f.key));

export interface FieldMappingEntry {
  source: string;
  target: string;
}

export interface DataFilter {
  field: string;
  operator: string;
  value?: any;
}

export type { TransformOperation, Transformation } from './transform-pipeline';

// ---------------------------------------------------------------------------
// Custom HTTP API connector — config blob shape
// ---------------------------------------------------------------------------

export interface CustomHttpFieldBinding {
  source_path: string;
  source_name: string;
}

export interface CustomHttpConfig {
  method: 'GET' | 'POST';
  url: string;
  static_query_params: Record<string, string>;
  static_body: Record<string, any> | null;
  headers: Record<string, string>;
  // When false, the connector skips TLS certificate verification on every
  // request (including the auth call). Use only for trusted internal hosts
  // with self-signed certs. Default true.
  verify_ssl: boolean;

  auth_type: 'none' | 'basic' | 'bearer' | 'api_key' | 'oauth2' | 'login_password';
  username?: string;
  password?: string;
  bearer_token?: string;
  api_key?: string;
  api_key_location: 'header' | 'query';
  api_key_name: string;
  api_key_prefix: string;
  token_url?: string;
  client_id?: string;
  client_secret?: string;
  scope?: string;
  // login_password auth (e.g. PocketBase / Beszel): POST {identity, password}
  // JSON to login_url, extract token via JSONPath, send on subsequent requests
  // as `<token_header>: <token_prefix><token>`.
  login_url?: string;
  identity_field?: string;
  identity?: string;
  password_field?: string;
  token_path?: string;
  token_header?: string;
  token_prefix?: string;
  token_ttl_seconds?: number;

  pagination_type: 'none' | 'offset' | 'page' | 'cursor' | 'link';
  page_size: number;
  offset_param: string;
  limit_param: string;
  page_param: string;
  perpage_param: string;
  start_page: number;
  total_path: string;
  cursor_request_param: string;
  cursor_response_path: string;

  records_root: string;
  field_bindings: CustomHttpFieldBinding[];
  // When true, the connector emits each record verbatim — no per-field
  // JSONPath bindings needed. The wizard's field-mapping step derives
  // source fields from sample records.
  passthrough: boolean;
  // How many levels of nested objects the wizard walks when discovering
  // source fields from a sample record. Only used when passthrough=true
  // (explicit bindings produce flat records). Default 3.
  passthrough_depth: number;
  max_pages: number;
  request_timeout_seconds: number;
}

export const DEFAULT_CUSTOM_HTTP_CONFIG: CustomHttpConfig = {
  method: 'GET',
  url: '',
  static_query_params: {},
  static_body: null,
  headers: {},
  verify_ssl: true,
  auth_type: 'none',
  api_key_location: 'header',
  api_key_name: 'X-API-Key',
  api_key_prefix: '',
  identity_field: 'identity',
  password_field: 'password',
  token_path: '$.token',
  token_header: 'Authorization',
  token_prefix: '',
  token_ttl_seconds: 900,
  pagination_type: 'none',
  page_size: 100,
  offset_param: 'offset',
  limit_param: 'limit',
  page_param: 'page',
  perpage_param: 'perPage',
  start_page: 1,
  total_path: '',
  cursor_request_param: 'cursor',
  cursor_response_path: '$.next_cursor',
  records_root: '',
  field_bindings: [],
  passthrough: true,
  passthrough_depth: 3,
  max_pages: 500,
  request_timeout_seconds: 30,
};

export interface WizardFormData {
  integration_type: string | null;
  source_method: string;
  name: string;
  description: string;
  category: string | null;
  enabled: boolean;
  logo: string | null;
  config: Record<string, any>;
  field_mapping: FieldMappingEntry[];
  transformations: Transformation[];
  preserve_fields: string[];
  data_filters: DataFilter[];
  schedule: { cron: string; timezone: string; enabled: boolean };
  // Review-step opt-in: run the asset-generation pipeline right after this
  // data source is created. UI only for now — wiring is pending backend
  // support (see the create onSuccess in IntegrationWizardPage).
  run_pipeline_after_create: boolean;
  preview_session_id: string | null;
  sampleData: Record<string, any>[] | null;
  sourceFields: string[] | null;
}

export interface WizardState {
  mode: WizardMode;
  currentStep: WizardStep;
  editIntegrationId: string | null;
  formData: WizardFormData;
  validation: Partial<Record<ValidatableStep, { valid: boolean; errors: string[] }>>;
  connectionTestStatus: 'idle' | 'testing' | 'success' | 'failed' | 'skipped';
  dirty: boolean;
}

export type WizardAction =
  | { type: 'SET_TYPE'; payload: string }
  | { type: 'SET_SOURCE_METHOD'; payload: string }
  | { type: 'SET_STEP'; payload: WizardStep }
  | { type: 'UPDATE_FORM'; payload: Partial<WizardFormData> }
  | { type: 'SET_VALIDATION'; payload: { step: ValidatableStep; valid: boolean; errors: string[] } }
  | { type: 'SET_CONNECTION_STATUS'; payload: WizardState['connectionTestStatus'] }
  | { type: 'LOAD_INTEGRATION'; payload: { integration: any; mode: WizardMode } }
  | { type: 'LOAD_SAVED'; payload: WizardState }
  | { type: 'RESET' };

const emptyFormData: WizardFormData = {
  integration_type: null,
  source_method: 'direct',
  name: '',
  description: '',
  category: null,
  enabled: true,
  logo: null,
  config: {},
  field_mapping: [],
  transformations: [],
  preserve_fields: [],
  data_filters: [],
  schedule: { cron: '0 0 * * *', timezone: 'UTC', enabled: true },
  run_pipeline_after_create: true,
  preview_session_id: null,
  sampleData: null,
  sourceFields: null,
};

export const initialState: WizardState = {
  mode: 'create',
  currentStep: 'type-select',
  editIntegrationId: null,
  formData: { ...emptyFormData },
  validation: {},
  connectionTestStatus: 'idle',
  dirty: false,
};

// Fields reset when the connector type or source method changes
const CLEARED_CONNECTION = {
  config: {} as Record<string, any>,
  sampleData: null as Record<string, any>[] | null,
  sourceFields: null as string[] | null,
  preview_session_id: null as string | null,
};

export function wizardReducer(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case 'SET_TYPE':
      return {
        ...state,
        formData: { ...state.formData, integration_type: action.payload, ...CLEARED_CONNECTION },
        currentStep: 1,
        dirty: true,
      };
    case 'SET_SOURCE_METHOD':
      return {
        ...state,
        formData: { ...state.formData, source_method: action.payload, ...CLEARED_CONNECTION },
        dirty: true,
      };
    case 'SET_STEP':
      return { ...state, currentStep: action.payload };
    case 'UPDATE_FORM':
      return { ...state, formData: { ...state.formData, ...action.payload }, dirty: true };
    case 'SET_VALIDATION':
      return {
        ...state,
        validation: {
          ...state.validation,
          [action.payload.step]: { valid: action.payload.valid, errors: action.payload.errors },
        },
      };
    case 'SET_CONNECTION_STATUS':
      return { ...state, connectionTestStatus: action.payload };
    case 'LOAD_INTEGRATION': {
      const { integration: i, mode } = action.payload;
      // Data-source-specific (preserve) fields are persisted as self-mappings
      // ({field: field}) — see buildFieldMappingPayload — AND, since #809, in a
      // distinct preserve_fields array. preserve_fields is authoritative: any
      // field listed there is DSS and must NOT be reconstructed as a framework
      // mapping, even when it is named exactly like a canonical framework field
      // (os, user, owner, model, location, ...). For legacy integrations saved
      // before preserve_fields existed (empty/absent), fall back to the
      // framework-name heuristic: a self-mapping whose target is not a framework
      // key is treated as preserved. Splitting these back out here makes them
      // render as "Data Source Specific" instead of an unrecognized framework
      // mapping (highlighted source card, missing badge, absent from the right
      // column).
      const authoritativePreserve = new Set<string>(i.preserve_fields || []);
      const fieldMapping: FieldMappingEntry[] = [];
      const preservedFromMapping: string[] = [];
      if (i.field_mapping) {
        for (const [target, source] of Object.entries(i.field_mapping)) {
          if (authoritativePreserve.has(target) && source === target) {
            // Authoritative DSS classification — pull the {field:field} self-map
            // that encodes this preserved field out of the framework-mapping
            // list. Requiring source === target keeps a genuine (non-self)
            // mapping to a same-named target from being silently dropped when an
            // out-of-band write leaves field_mapping and preserve_fields
            // inconsistent (the wizard's own save only ever writes self-maps).
            continue;
          }
          if (source === target && !FRAMEWORK_FIELD_KEYS.has(target)) {
            preservedFromMapping.push(target);
          } else {
            fieldMapping.push({ source: source as string, target });
          }
        }
      }
      const transformations: Transformation[] = i.transformations
        ? Object.entries(i.transformations as Record<string, any>).map(
            ([name, def]) => ({
              name,
              operations: ((def as any).operations ?? []).map((op: any) => ({
                ...op,
                id: generateOpId(),
              })) as TransformOperation[],
            }),
          )
        : [];
      return {
        ...state,
        mode,
        editIntegrationId: mode === 'edit' ? i.id : null,
        currentStep: 1,
        formData: {
          integration_type: i.integration_type,
          source_method: i.source_method ?? 'direct',
          name: mode === 'clone' ? `${i.name} (Copy)` : i.name,
          description: i.description || '',
          category: i.data_source_category || i.category || null,
          enabled: i.status === 'enabled',
          logo: i.logo && !i.logo.startsWith('/') ? `/logos/${i.logo}` : i.logo,
          config: i.config || {},
          field_mapping: fieldMapping,
          transformations,
          preserve_fields: [
            ...new Set([...(i.preserve_fields || []), ...preservedFromMapping]),
          ],
          data_filters: i.data_filters || [],
          schedule: {
            cron: i.schedule_cron || '',
            timezone: i.schedule_timezone || 'UTC',
            enabled: i.schedule_enabled || false,
          },
          run_pipeline_after_create: true,
          preview_session_id: null,
          sampleData: null,
          sourceFields: null,
        },
        connectionTestStatus: 'idle',
        dirty: false,
      };
    }
    case 'LOAD_SAVED': {
      const saved = action.payload;
      // Migrate stale localStorage: old format used { operation, sources, target }
      const hasStaleTransforms = saved.formData?.transformations?.length > 0
        && !Array.isArray((saved.formData.transformations[0] as any).operations);
      return {
        ...saved,
        formData: hasStaleTransforms
          ? { ...saved.formData, transformations: [] }
          : saved.formData,
        dirty: false,
      };
    }
    case 'RESET':
      return { ...initialState, formData: { ...emptyFormData } };
    default:
      return state;
  }
}

// localStorage persistence
const STORAGE_KEY = 'integrationWizardState';
const EXPIRY_MS = 24 * 60 * 60 * 1000;

export function saveWizardState(state: WizardState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ state, timestamp: Date.now() }));
  } catch { /* ignore */ }
}

export function loadWizardState(): WizardState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const { state, timestamp } = JSON.parse(raw);
    if (Date.now() - timestamp > EXPIRY_MS) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return state;
  } catch {
    return null;
  }
}

export function clearWizardState(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch { /* ignore */ }
}

export const STEP_LABELS: Record<number, string> = {
  1: 'Basic Details',
  2: 'Connection',
  3: 'Field Mapping',
  4: 'Review',
};
