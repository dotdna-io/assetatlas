// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo } from 'react';
import { useMutation, useQueryClient, type UseMutationOptions } from '@tanstack/react-query';
import { apiClient } from './client';
import { useApiQuery } from './hooks';
import { encodeFieldId, type FieldId } from '@/types/field-id';

export type IdentifierType = 'hostname' | 'fqdn' | 'mac' | 'serial';
export type MatchType = 'exact' | 'prefix' | 'suffix' | 'range';

// Mirrors MATCH_TYPES_BY_TYPE in src/modules/merge_denylist/encoding.py — the
// pairs DenylistEntryCreate, the encoder and migration 135's CHECK all enforce.
// Kept here so the selector cannot offer a pair the API would 400 on: only mac
// takes a range, and a serial has no pattern form at all.
export const MATCH_TYPES_BY_TYPE: Record<IdentifierType, readonly MatchType[]> = {
  hostname: ['exact', 'prefix', 'suffix'],
  fqdn: ['exact', 'prefix', 'suffix'],
  mac: ['exact', 'prefix', 'range'],
  serial: ['exact'],
};

export interface DenylistEntry {
  id: string;
  identifier_type: IdentifierType;
  match_type: MatchType;
  identifier_value: string;
  raw_value: string;
  reason: string | null;
  created_by: string;
  enabled: boolean;
  created_at: string;
}

// A range carries its two bounds and no raw_value; every other match type
// carries raw_value and no bounds. DenylistEntryCreate._check_entry_shape 400s
// on any other combination, so the two shapes are a union rather than one type
// with four optional fields — a body missing both must not compile.
export type DenylistCreateBody = {
  identifier_type: IdentifierType;
  reason?: string | null;
} & (
  | { match_type: 'range'; range_start: string; range_end: string; raw_value?: never }
  | {
      match_type: Exclude<MatchType, 'range'>;
      raw_value: string;
      range_start?: never;
      range_end?: never;
    }
);

export const mergeDenylistApi = {
  list: () => apiClient.get<{ entries: DenylistEntry[] }>('/merge-denylist'),
  create: (body: DenylistCreateBody) => apiClient.post<DenylistEntry>('/merge-denylist', body),
  remove: (id: string) => apiClient.del<{ ok: boolean }>('/merge-denylist/' + id),
};

export function useMergeDenylist() {
  return useApiQuery<{ entries: DenylistEntry[] }>(['merge-denylist'], '/merge-denylist');
}

export function useCreateDenylistEntry(
  options?: Omit<UseMutationOptions<DenylistEntry, Error, DenylistCreateBody>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<DenylistEntry, Error, DenylistCreateBody>({
    mutationFn: (body) => mergeDenylistApi.create(body),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['merge-denylist'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

export function useDeleteDenylistEntry(
  options?: Omit<UseMutationOptions<{ ok: boolean }, Error, string>, 'mutationFn'>,
) {
  const qc = useQueryClient();
  const { onSuccess, ...rest } = options || {};
  return useMutation<{ ok: boolean }, Error, string>({
    mutationFn: (id) => mergeDenylistApi.remove(id),
    onSuccess: (...args) => {
      qc.invalidateQueries({ queryKey: ['merge-denylist'] });
      onSuccess?.(...args);
    },
    ...rest,
  });
}

// ---------------------------------------------------------------------------
// Match-count preview (#991)
// ---------------------------------------------------------------------------
//
// "This entry matches N of your M MAC addresses", before the entry exists — the
// only thing that catches an entry which is legal but far too broad. There is no
// preview endpoint, so this counts through GET /api/assets/values, whose payload
// is a field's distinct values plus a total_distinct for the whole column. Two
// properties of that endpoint shape everything below:
//
//   * total_distinct ignores `search` (it does honour `filters`), so M has to
//     come from its own unnarrowed call.
//   * `values` is capped server-side at 500 (AssetValuesParams.limit, le=500),
//     so N is a floor rather than a total once the cap is hit — hence `capped`.
//
// It counts identifier values, not assets: a denylist entry removes identifier
// components, so components are the honest unit and the one the alarm is about.

// assets.hostname/fqdn/mac/sn — the text[] columns holding every value an asset
// carries from every source, which is the component space the denylist filters.
// The display names are catalog.py::STATIC_FIELDS keys; both they and
// MATCH_TYPES_BY_TYPE are pinned against Python by
// tests/fixtures/merge-denylist-match-types.json.
export const PREVIEW_FIELD_ID: Record<IdentifierType, FieldId> = {
  hostname: { kind: 'static', name: 'Hostname' },
  fqdn: { kind: 'static', name: 'FQDN' },
  mac: { kind: 'static', name: 'MAC Address' },
  serial: { kind: 'static', name: 'Serial Number' },
};

/** GET /assets/values caps `limit` server-side (AssetValuesParams: le=500). */
export const PREVIEW_LIMIT = 500;

const MAC_HEX_LEN = 12;

// The endpoint's real payload. NB assets.ts's FieldValuesResponse calls the last
// field `total`; the backend has always sent `total_distinct`
// (asset_service.get_field_values) and nothing reads the mistyped one.
interface FieldValuesPayload {
  field: string;
  values: { value: string; count: number }[];
  total_distinct: number;
}

export interface DenylistDraft {
  identifier_type: IdentifierType;
  match_type: MatchType;
  raw_value: string;
  range_start: string;
  range_end: string;
}

export interface DenylistMatchPreview {
  matched: number;
  total: number;
  /** The 500-value cap was hit, so `matched` is a floor and not a total. */
  capped: boolean;
}

const macHex = (raw: string) => raw.replace(/[^0-9a-z]/gi, '').toLowerCase();

const isHex = (s: string) => /^[0-9a-f]+$/.test(s);

// 'bc2411' -> 'bc:24:11'; 'bc24111' -> 'bc:24:11:1'. assets.mac stores MACs in
// the canonical colon-grouped form, so a hex prefix grouped the same way is a
// literal string prefix of the stored value — which is what an ILIKE `search`
// needs. Same grouping as encoding.py::_mac_prefix, dangling nibble and all.
const groupMacHex = (hex: string) => (hex.match(/.{1,2}/g) ?? []).join(':');

function commonHexPrefix(a: string, b: string): string {
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i += 1;
  return a.slice(0, i);
}

interface PreviewPlan {
  field: string;
  /** Value-level narrowing: the endpoint renders it as `x ILIKE '<search>%'`. */
  search?: string;
  /** Asset-level narrowing, for the one shape `search` cannot express. */
  filters?: string;
  matches: (value: string) => boolean;
}

// null == nothing worth asking the server. That has to cover every entry the
// encoder refuses, not just the half-typed ones: an invalid entry would
// otherwise fetch nothing and be reported as "matches none of your 25,913 MAC
// addresses", which reads as a narrow pattern rather than a broken one. So the
// refusals below track encoding.py's — deliberately including its asymmetries
// (an exact MAC of 'zz:..' IS storable, a 'zz:..:*' prefix is not; a serial
// takes a literal '*' because it has no pattern form to mistake it for).
// The encoder still has the last word on save; this only decides whether there
// is a number worth showing.
//
// `search` is deliberately allowed to over-fetch. The endpoint does not escape
// it, so a '_' in a hostname acts as a LIKE wildcard and widens the candidate
// set; `matches` runs over whatever comes back, so a wider net stays correct and
// only costs cap headroom.
function buildPreviewPlan(draft: DenylistDraft): PreviewPlan | null {
  const field = encodeFieldId(PREVIEW_FIELD_ID[draft.identifier_type]);
  const isMac = draft.identifier_type === 'mac';

  if (draft.match_type === 'range') {
    const startHex = macHex(draft.range_start);
    const endHex = macHex(draft.range_end);
    if (startHex.length !== MAC_HEX_LEN || endHex.length !== MAC_HEX_LEN) return null;
    // A bound has to be hex to be an integer at all (_mac_bound says the same).
    // parseInt stops at the first non-hex char rather than failing, so
    // 'bc:24:11:zz:zz:zz' would silently become the bound 0xbc2411.
    if (!isHex(startHex) || !isHex(endHex)) return null;
    const lo = parseInt(startHex, 16);
    const hi = parseInt(endHex, 16);
    if (hi < lo) return null;
    // Bounds are numeric and the column is text, so no operator can express the
    // range: narrow by the tightest prefix enclosing it and do the arithmetic
    // here. 48 bits is well inside a JS integer.
    const enclosing = groupMacHex(commonHexPrefix(startHex, endHex));
    return {
      field,
      search: enclosing || undefined,
      matches: (v) => {
        const hex = macHex(v);
        if (!isHex(hex)) return false;
        const n = parseInt(hex, 16);
        return n >= lo && n <= hi;
      },
    };
  }

  const raw = draft.raw_value.trim();
  if (!raw) return null;

  if (draft.match_type === 'prefix') {
    if (!raw.endsWith('*') || raw.slice(0, -1).includes('*')) return null;
    const literal = raw.slice(0, -1);
    if (!literal) return null;
    let needle = literal.toLowerCase();
    if (isMac) {
      // _mac_prefix refuses a non-hex or over-long prefix outright. Note it does
      // NOT refuse a short one here: the OUI floor is worth previewing, because
      // "at least 500 of your 25,913" is exactly why 'b*' is a bad idea.
      const hex = macHex(literal);
      if (!isHex(hex) || hex.length > MAC_HEX_LEN) return null;
      needle = groupMacHex(hex);
    }
    return { field, search: needle, matches: (v) => v.toLowerCase().startsWith(needle) };
  }

  if (draft.match_type === 'suffix') {
    if (!raw.startsWith('*') || raw.slice(1).includes('*')) return null;
    const needle = raw.slice(1).toLowerCase();
    if (!needle) return null;
    // `search` is prefix-only, so narrow by asset instead: `ends` renders as
    // EXISTS (SELECT 1 FROM unnest(col) x WHERE x ILIKE '%needle'), i.e. only
    // assets that definitely hold a match. Their values are then filtered here
    // down to the ones that really match, since the endpoint hands back every
    // value those assets carry, not just the matching ones.
    return {
      field,
      filters: JSON.stringify([
        { field_id: PREVIEW_FIELD_ID[draft.identifier_type], op: 'ends', value: needle },
      ]),
      matches: (v) => v.toLowerCase().endsWith(needle),
    };
  }

  if (isMac) {
    // No hex check: canonical_mac_component only cleans to alnum, so the
    // encoder really does store an exact 'zz:zz:zz:zz:zz:zz'.
    const hex = macHex(raw);
    if (hex.length < MAC_HEX_LEN) return null;
    const needle = groupMacHex(hex.slice(0, MAC_HEX_LEN));
    return { field, search: needle, matches: (v) => v.toLowerCase() === needle };
  }
  // An exact hostname/fqdn cannot hold a '*' — that is a mis-picked match_type
  // the encoder rejects, not a value that happens to match nothing. A serial has
  // no prefix/suffix form, so there a '*' is just a character.
  if (draft.identifier_type !== 'serial' && raw.includes('*')) return null;
  const needle = raw.toLowerCase();
  return { field, search: needle, matches: (v) => v.toLowerCase() === needle };
}

export function useDenylistMatchPreview(draft: DenylistDraft | null) {
  const plan = useMemo(() => (draft ? buildPreviewPlan(draft) : null), [draft]);

  // M — the whole column, unnarrowed. total_distinct ignores `search`, so the
  // candidate call's own copy of it would be this same number anyway.
  const totalQuery = useApiQuery<FieldValuesPayload>(
    ['merge-denylist', 'preview-total', plan?.field],
    '/assets/values',
    { params: { field: plan?.field, limit: 1 }, enabled: !!plan, staleTime: 5 * 60 * 1000 },
  );

  const candidateQuery = useApiQuery<FieldValuesPayload>(
    ['merge-denylist', 'preview-values', plan?.field, plan?.search, plan?.filters],
    '/assets/values',
    {
      params: {
        field: plan?.field,
        limit: PREVIEW_LIMIT,
        search: plan?.search,
        filters: plan?.filters,
      },
      enabled: !!plan,
    },
  );

  const values = candidateQuery.data?.values;
  const total = totalQuery.data?.total_distinct;
  // Memoised: the tab re-renders on every keystroke in the reason field, and
  // the count only moves when the plan or the fetched values do.
  const data = useMemo<DenylistMatchPreview | undefined>(() => {
    if (!plan || !values || total === undefined) return undefined;
    return {
      matched: values.filter((v) => plan.matches(v.value)).length,
      total,
      capped: values.length >= PREVIEW_LIMIT,
    };
  }, [plan, values, total]);

  return {
    data,
    // A disabled query sits in `pending` forever, so the plan gates this too.
    isPending: !!plan && (totalQuery.isPending || candidateQuery.isPending),
    isError: totalQuery.isError || candidateQuery.isError,
    error: totalQuery.error ?? candidateQuery.error,
  };
}
