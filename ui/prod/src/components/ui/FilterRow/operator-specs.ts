// SPDX-License-Identifier: AGPL-3.0-or-later
import type { FieldCatalogEntry, OperatorSpec } from '@/types/field-id';

/** Normalize a label/code to the underscored wire form used pre-Phase-2
 *  ("not equals" -> "not_equals"). Codes ("eq") pass through unchanged. */
export function normalizeOperator(op: string): string {
  return op.toLowerCase().replace(/\s+/g, '_');
}

/** Operators that take no value (arity 'none'): the canonical codes the dropdown
 *  emits plus the legacy spaced/underscored presence labels still held by stored
 *  rules. The wizard readiness gates (isFilterReady/isRowReady) and the value
 *  control key off this one set. Mirrors the arity:'none' operators in
 *  src/modules/fields/operators.py. */
export const VALUELESS_OPERATORS = new Set([
  'empty', 'notempty', 'is_true', 'is_false',
  'is_empty', 'is_not_empty',
]);

export function isValuelessOperator(op: string): boolean {
  return VALUELESS_OPERATORS.has(normalizeOperator(op));
}

/** Is a filter condition complete enough to save / count / preview?
 *  - valueless ops (empty/notempty/...) are always ready;
 *  - `between` needs BOTH bounds of its "lo,hi" value (a half-filled range is
 *    not ready — the SQL builder would otherwise drop the clause and widen the
 *    result set);
 *  - `within_last` needs a numeric amount in its "<n><unit>" value (a bare unit
 *    like "d" is not ready);
 *  - every other op just needs a non-empty value.
 *  Shared by the rule/posture readiness gates so the bespoke scalar-helper
 *  widgets (which may hold a transient partial value) are gated consistently. */
export function isFilterValueReady(operator: string, value: unknown): boolean {
  if (isValuelessOperator(operator)) return true;
  const code = normalizeOperator(operator);
  // List ops (any/all/none) and between now carry a real array; between needs
  // BOTH bounds, list ops need at least one non-empty member.
  if (Array.isArray(value)) {
    const members = value.map((m) => (m == null ? '' : String(m).trim()));
    if (code === 'between') {
      return members.length === 2 && Boolean(members[0]) && Boolean(members[1]);
    }
    return members.some(Boolean);
  }
  const v = typeof value === 'string' ? value.trim() : value == null ? '' : String(value);
  if (!v) return false;
  // Legacy comma-string fallback (pre-array stored rules).
  if (code === 'between') {
    const [lo, hi] = v.split(',');
    return Boolean(lo && lo.trim() && hi && hi.trim());
  }
  if (code === 'within_last') {
    return /\d/.test(v);
  }
  return true;
}

/** Find the spec for an operator value, matching its `code` then its `aliases`
 *  (so a pre-migration "in" resolves to the "any" spec). Shared by FilterRow's
 *  dropdown selection and operatorLabel's display resolution. */
export function specFor(op: string, specs: OperatorSpec[]): OperatorSpec | undefined {
  const norm = normalizeOperator(op);
  return specs.find(
    (s) => s.code === op || s.code === norm || s.aliases.includes(op) || s.aliases.includes(norm),
  );
}

/**
 * Ordered operator specs for a catalog entry — the wire `operator_specs`
 * (already resolved + ordered, element 0 = default). Every entry now carries
 * operator_specs: the API populates it and the only client-side synthesizer
 * (reporting's QuestionRenderer) builds it directly, so the deprecated
 * `operators: string[]` field and its legacy-label fallback were removed in
 * Phase 3.
 */
export function deriveOperatorSpecs(entry: FieldCatalogEntry | undefined): OperatorSpec[] {
  if (!entry) return [];
  return entry.operator_specs ?? [];
}

/**
 * Resolve a stored operator value to a human label for display via the field's
 * spec (code or alias, so a pre-migration "in" shows the "any" label). Falls
 * back to the raw value.
 */
export function operatorLabel(op: string, entry: FieldCatalogEntry | undefined): string {
  const hit = specFor(op, deriveOperatorSpecs(entry));
  return hit ? hit.label : op;
}
