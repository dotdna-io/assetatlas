# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Reclassify Service

Handles manual reclassification of asset category and subcategory.
Validates against categories config, updates the asset record,
persists the override, and writes a chronicle event.
"""

import json
import logging
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from uuid import UUID

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.asset_generation.repositories.category_override_repository import (
    CategoryOverrideRepository,
)
from src.modules.assets.repositories.custom_subcategory_repository import (
    CustomSubcategoryRepository,
)
from src.modules.fields.unified.materialize import (
    is_materializable,
    outside_request_scope,
    refresh_unified_values,
)
from src.modules.fields.unified.repository import UnifiedFieldsRepository
from src.modules.shared import asset_data_version
from src.modules.shared.field_resolver_factory import build_field_resolver

logger = logging.getLogger(__name__)

CATEGORIES_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "data", "categories.json"
)

_MAX_SUBCATEGORY_LEN = 64
# Excludes control chars and non-ASCII so a name can't carry hidden
# whitespace or break downstream log/CSV consumers.
_PRINTABLE_ASCII_RE = re.compile(r"^[\x20-\x7e]+$")


def _coalesce(*values: Any) -> Any:
    """First non-None value — SQL COALESCE, in Python.

    The chronicle event for a cleared override has to state what the UPDATE
    actually wrote, and that statement COALESCEs the predicted snapshot against
    the live value. Computing the event any other way makes asset history
    contradict the assets table on exactly the rows where the snapshot is
    missing, and nothing would ever flag it (#1154).
    """
    for value in values:
        if value is not None:
            return value
    return None


def _canonical_asset_id(asset_id: Any) -> str:
    """An asset id in the one spelling this service can match against.

    psycopg2 renders a `uuid` column as canonical lowercase 8-4-4-4-12, always,
    so every dict and set built off a query result here is keyed that way and
    looking one up is a plain string comparison. Postgres accepts — and
    canonicalises on cast — spellings `uuid.UUID()` also parses (`A0EEBC99-…`,
    `{a0eebc99-…}`, unhyphenated), so a caller can construct one legitimately
    and match nothing. Every failure that follows is silent: the writes still
    land, because they go through `::uuid` casts Postgres canonicalises, while
    `clear_overrides_bulk` clears nothing and answers 200 and `reclassify_bulk`
    records `"old": None` in durable history (#1275).

    `urn:uuid:…` is the exception in the other direction: Python parses it,
    Postgres **rejects** it, so it never reached a keyed lookup — it 500'd at the
    cast (#973). Canonicalising here happens to fix that too, but it is a side
    effect, not the reason, and it is why the blueprint's `ensure_uuid` is not
    redundant with this: that guards the HTTP boundary so a client typo is a 400
    rather than a 500, whereas this holds for the entry points below whatever the
    caller — scripts, MCP tools, future ones that never pass through a route.

    Called at each entry point that keys a lookup off a query result, so the
    private helpers below can take the invariant for granted rather than every
    caller having to remember it. `reclassify_asset` (single) deliberately does
    not: it reads its old values straight off the row `_get_current_asset`
    returned, so it has no keyed lookup to miss.

    Raises ValueError on anything that is not a UUID — mapped to a 400 by
    `@handle_errors`. Skipping it instead would drop assets from a batch the
    user asked for and report the shortfall as success, which is the same
    failure this exists to remove.
    """
    try:
        return str(UUID(str(asset_id)))
    except ValueError:
        # `from None`: UUID's own "badly formed hexadecimal UUID string" says
        # nothing this message doesn't, and chaining it buries the id that was
        # rejected under a "During handling of the above exception" preamble.
        raise ValueError(
            f"Invalid asset ID: {asset_id!r}. Expected a UUID like "
            "'550e8400-e29b-41d4-a716-446655440000'."
        ) from None


def _canonical_asset_ids(asset_ids: Iterable[Any]) -> List[str]:
    """Every id in a batch, canonically spelled and each asset named once — see
    `_canonical_asset_id`. One bad id fails the batch, matching `_bulk_scope`'s
    all-or-nothing writes.

    The dedupe is not tidiness: canonicalising is what makes two spellings of one
    asset collide in the first place (`['A0EEBC99-…', 'a0eebc99-…']` used to be
    two strings of which only one ever matched). Left in, the asset would be
    deactivated twice, counted twice, and given two identical
    `category_override_cleared` events at the same timestamp — durable wrong
    history arriving through the fix for durable wrong history.

    `dict.fromkeys` rather than `set`: batch order decides the order of the
    chronicle events and of the ids bound into each statement, and there is no
    reason for it to shuffle between runs.
    """
    return list(dict.fromkeys(_canonical_asset_id(a) for a in asset_ids))


class ReclassifyService:
    """Service for manual asset category reclassification."""

    def __init__(
        self,
        db_adapter: PostgreSQLAdapter,
        override_repo: CategoryOverrideRepository,
        custom_subcategory_repo: CustomSubcategoryRepository,
    ):
        self.db = db_adapter
        self.override_repo = override_repo
        self.custom_subcategory_repo = custom_subcategory_repo
        self._catalog: List[Dict[str, Any]] = self._load_categories()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_categories(self) -> List[Dict[str, Any]]:
        """Return catalog categories with custom subcategories appended
        per category. Catalog entries win on case-insensitive name
        collisions so a future curated entry never gets shadowed.
        """
        custom = self.custom_subcategory_repo.get_all()
        merged: List[Dict[str, Any]] = []
        for entry in self._catalog:
            cat_name = entry["name"]
            extras_for_cat = custom.get(cat_name, [])
            if not extras_for_cat:
                merged.append(entry)
                continue
            catalog_subs = list(entry["subcategories"])
            existing_lower = {s.lower() for s in catalog_subs}
            extras = [s for s in extras_for_cat if s.lower() not in existing_lower]
            merged.append({"name": cat_name, "subcategories": catalog_subs + extras})
        return merged

    def add_custom_subcategory(
        self,
        category: str,
        name: str,
        user: str,
    ) -> Dict[str, Any]:
        """Persist a new subcategory under ``category``.

        Raises ValueError on invalid input or duplicates; the caller maps
        these to HTTP 400.
        """
        cleaned = self._validate_custom_subcategory(category, name)
        self.custom_subcategory_repo.add(category, cleaned, user)
        logger.info(
            f"Custom subcategory added: category={category!r} name={cleaned!r} by={user}"
        )
        return {
            "category": category,
            "subcategory": cleaned,
            "created_by": user,
        }

    # ------------------------------------------------------------------
    # Main reclassification flow
    # ------------------------------------------------------------------

    def reclassify_asset(
        self,
        asset_id: str,
        category: str,
        subcategory: str,
        user: str,
    ) -> Dict[str, Any]:
        """
        Reclassify an asset's category and subcategory.

        Args:
            asset_id: Asset UUID string
            category: New category name
            subcategory: New subcategory name
            user: Username performing the reclassification

        Returns:
            Dict with reclassification details

        Raises:
            ValueError: If category or subcategory is invalid
            LookupError: If asset not found
        """
        self._validate_category(category, subcategory)

        # Fetch current asset
        current = self._get_current_asset(asset_id)
        if not current:
            raise LookupError(f"Asset not found: {asset_id}")

        previous_category = current.get("category")
        previous_subcategory = current.get("subcategory")
        now = datetime.now(timezone.utc)

        # Update assets table
        self._update_asset(asset_id, category, subcategory, now)

        # Build features snapshot to freeze asset state at correction time
        features_snapshot = {
            "manufacturer": current.get("manufacturer", ""),
            "model": current.get("model", ""),
            "os_normalized": current.get("os_normalized", ""),
            "mac_vendor": current.get("mac_vendor", ""),
            "vendor_category": current.get("vendor_category", ""),
        }

        # Save override (upsert into category_overrides)
        self.override_repo.save_override(
            asset_id=asset_id,
            category=category,
            subcategory=subcategory,
            reason=f"Manual reclassification by {user}",
            overridden_by=user,
            features_snapshot=features_snapshot,
        )

        # Write chronicle event
        self.db.bulk_write_chronicle_events([{
            "time": now,
            "asset_id": asset_id,
            "event_type": "manual_reclassification",
            "changed_attributes": ["category", "subcategory"],
            "attribute_changes": {
                "category": {"old": previous_category, "new": category},
                "subcategory": {"old": previous_subcategory, "new": subcategory},
            },
            "causation": f"Manual reclassification by {user}",
        }])

        logger.info(
            f"Asset {asset_id} reclassified: "
            f"{previous_category}/{previous_subcategory} -> {category}/{subcategory} "
            f"by {user}"
        )

        self._refresh_unified_values([asset_id])
        # Category/subcategory values changed — invalidate the Explorer
        # field-values cache (#1410).
        asset_data_version.bump()

        return {
            "id": asset_id,
            "category": category,
            "subcategory": subcategory,
            "previous_category": previous_category,
            "previous_subcategory": previous_subcategory,
            "reclassified_by": user,
            "reclassified_at": now.isoformat(),
        }

    def reclassify_bulk(
        self,
        asset_ids: List[str],
        category: str,
        subcategory: str,
        user: str,
    ) -> Dict[str, Any]:
        """Apply one category/subcategory to many assets in a single
        transaction (all-or-nothing). Validates once up front so a bad
        category never opens a scope.
        """
        self._validate_category(category, subcategory)
        if not asset_ids:
            return {"updated": 0}

        # Before anything keys off them: `previous` below is keyed by psycopg2's
        # canonical spelling, so a caller's own would silently read every
        # old-value as None while the writes landed anyway (#1275).
        asset_ids = _canonical_asset_ids(asset_ids)
        now = datetime.now(timezone.utc)

        # Snapshot + writes share one transaction so the chronicle old-values
        # reflect exactly what this operation overwrote (no read-then-write race
        # against a concurrent reclassify).
        with self._bulk_scope():
            previous = self._fetch_current_categories(asset_ids)
            self.override_repo.save_overrides_bulk(
                asset_ids=asset_ids,
                category=category,
                subcategory=subcategory,
                reason=f"Bulk reclassification by {user}",
                overridden_by=user,
            )
            self._update_assets_bulk(asset_ids, category, subcategory, now)
            self.db.bulk_write_chronicle_events([
                {
                    "time": now,
                    "asset_id": aid,
                    "event_type": "manual_reclassification",
                    "changed_attributes": ["category", "subcategory"],
                    "attribute_changes": {
                        "category": {"old": previous.get(aid, {}).get("category"), "new": category},
                        "subcategory": {"old": previous.get(aid, {}).get("subcategory"), "new": subcategory},
                    },
                    "causation": f"Bulk reclassification by {user}",
                }
                for aid in asset_ids
            ])

        logger.info(
            f"Bulk reclassified {len(asset_ids)} assets -> {category}/{subcategory} by {user}"
        )
        # _bulk_scope's exit only closed its NESTED scope — under Flask the
        # request scope is still open, so the refresh must leave it (via
        # outside_request_scope in the helper) to make the reclassify durable
        # before the hook runs; a hook DB-error then cannot roll it back.
        self._refresh_unified_values(asset_ids)
        # Category/subcategory values changed — invalidate the Explorer
        # field-values cache (#1410).
        asset_data_version.bump()
        return {"updated": len(asset_ids)}

    def get_override_state(self, asset_id: str) -> Dict[str, Any]:
        """Whether this asset carries a manual reclassification, who made it, and
        what the pipeline predicted — everything the edit modal needs to say so
        and to offer a restore (#1155).

        Sourced from `category_overrides`, NOT `assets.category_is_overridden`:
        that flag reads false on every override older than one pipeline run, so a
        read keyed on it would report "never reclassified" for almost every
        reclassified asset (#1270). The predicted pair comes from the same
        `_fetch_current_categories` snapshot `clear_overrides_bulk` uses, so what
        the tab offers to restore and what the restore actually writes agree by
        construction rather than by two copies of the same SELECT.

        Both predicted values can be None on rows written before migration 143 —
        the restore then degrades to "leave the values alone" (see
        `_reset_assets_bulk`), and reporting the absence lets the client say so
        instead of promising a change it won't get.

        Returns a well-formed body for an unknown asset rather than raising, the
        way the asset-label override read does: the caller renders it, and a
        missing asset is indistinguishable from an unoverridden one here.
        """
        asset_id = _canonical_asset_id(asset_id)
        override = self.override_repo.get_active_override(asset_id)
        current = self._fetch_current_categories([asset_id]).get(asset_id, {})
        return {
            "asset_id": asset_id,
            "is_overridden": override is not None,
            "overridden_by": override.get("overridden_by") if override else None,
            "overridden_at": override.get("overridden_at") if override else None,
            "reason": override.get("reason") if override else None,
            "category_predicted": current.get("category_predicted"),
            "subcategory_predicted": current.get("subcategory_predicted"),
        }

    def clear_overrides_bulk(self, asset_ids: List[str], user: str) -> Dict[str, Any]:
        """Deactivate category overrides for the matching assets that actually
        carry one, and reset those live rows to the category/subcategory the
        pipeline predicted. Assets in the filter set that were never overridden
        are left untouched — there is nothing to clear, and touching them would
        emit a spurious audit event. The definitive un-override lands at the next
        pipeline run (stage s13 stops applying inactive rows).

        "Actually carries one" is answered by `category_overrides.active`, the
        durable record — see the note at `_update_asset` for why the live flag
        cannot answer it (#1270).
        """
        if not asset_ids:
            return {"cleared": 0}

        # Before the narrowing below, which is a string comparison against
        # psycopg2's canonical spelling: any other one matches nothing, clears
        # nothing, and still answers `{"cleared": 0}` with a 200 (#1275).
        asset_ids = _canonical_asset_ids(asset_ids)
        now = datetime.now(timezone.utc)
        cleared = 0

        with self._bulk_scope():
            previous = self._fetch_current_categories(asset_ids)
            # Only act on assets with an active override; clearing a never-overridden
            # asset would wipe its real subcategory and emit a spurious audit event.
            # Sourced from category_overrides, NOT assets.category_is_overridden:
            # that flag reads false on every override older than one pipeline run
            # and was never set at all by the single-asset path, so keying on it
            # made this whole method a silent no-op (#1270).
            active = self.override_repo.get_active_override_ids(asset_ids)
            overridden_ids = [aid for aid in asset_ids if aid in active]
            if overridden_ids:
                self.override_repo.deactivate_overrides_bulk(overridden_ids)
                self._reset_assets_bulk(overridden_ids, now)
                self.db.bulk_write_chronicle_events([
                    {
                        "time": now,
                        "asset_id": aid,
                        "event_type": "category_override_cleared",
                        "changed_attributes": ["category", "subcategory"],
                        # "new" mirrors _reset_assets_bulk's COALESCE exactly: on a
                        # row with no snapshot the UPDATE leaves the field alone, so
                        # the event has to say "unchanged", not None.
                        "attribute_changes": {
                            "category": {
                                "old": previous.get(aid, {}).get("category"),
                                "new": _coalesce(
                                    previous.get(aid, {}).get("category_predicted"),
                                    previous.get(aid, {}).get("category"),
                                ),
                            },
                            "subcategory": {
                                "old": previous.get(aid, {}).get("subcategory"),
                                "new": _coalesce(
                                    previous.get(aid, {}).get("subcategory_predicted"),
                                    previous.get(aid, {}).get("subcategory"),
                                ),
                            },
                        },
                        "causation": f"Bulk category override cleared by {user}",
                    }
                    for aid in overridden_ids
                ])
                cleared = len(overridden_ids)

        logger.info(
            f"Bulk cleared category overrides for {cleared} of {len(asset_ids)} matched assets by {user}"
        )
        if overridden_ids:
            self._refresh_unified_values(overridden_ids)
            # Restored categories change the distinct values — invalidate the
            # Explorer field-values cache (#1410).
            asset_data_version.bump()
        return {"cleared": cleared}

    @contextmanager
    def _bulk_scope(self):
        """Run a block of bulk writes in one all-or-nothing transaction scope.

        Centralizes the begin/commit/rollback dance so every bulk method shares
        the same error handling (a future copy-paste can't forget the
        end_scope(error=...) rollback on the failure path).
        """
        self.db.conn_manager.begin_scope()
        try:
            yield
        except Exception as exc:
            self.db.conn_manager.end_scope(error=exc)
            raise
        else:
            self.db.conn_manager.end_scope()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _refresh_unified_values(self, asset_ids: List[str]) -> None:
        """Recompute stored unified-field values for the reclassified assets.

        category/subcategory are unified-field inputs, so the materialized
        values of the touched rows must be refreshed — per-asset, hence never
        stamped. ALL materializable fields are refreshed rather than only
        static:Category/static:Subcategory-sourced ones: the per-asset UPDATE
        is one statement either way, and a single helper shape covers both
        this and the merge hook. Runs inside outside_request_scope: the
        caller's writes (single-asset UPDATEs, or the bulk scope's work whose
        inner end_scope does NOT commit under Flask) commit at the scope exit
        FIRST, so a hook failure can neither roll the user's reclassify back
        nor poison its transaction; on failure the affected stamps are
        cleared — durably, on the per-call path — so the resolver falls back
        to the live merge expression until the next pipeline run
        re-materializes.
        """
        slugs: List[str] = []
        with outside_request_scope(self.db.conn_manager):
            try:
                defs = UnifiedFieldsRepository(self.db).list_for_catalog()
                slugs = [d["slug"] for d in defs if is_materializable(d)]
                if not slugs:
                    return
                refresh_unified_values(
                    self.db, build_field_resolver(self.db), defs, asset_ids=asset_ids)
            except Exception:
                logger.warning(
                    "materialized unified-value refresh failed for %d asset(s) — "
                    "clearing materialized stamps (live SQL until the next "
                    "pipeline run)", len(asset_ids), exc_info=True)
                if slugs:
                    try:
                        UnifiedFieldsRepository(self.db).set_materialized_at(slugs, False)
                    except Exception:
                        logger.warning(
                            "could not clear materialized_at for %s", slugs, exc_info=True)

    def _load_categories(self) -> List[Dict[str, Any]]:
        """Load categories from data/categories.json."""
        path = os.path.normpath(CATEGORIES_CONFIG_PATH)
        if not os.path.exists(path):
            logger.warning(f"Categories config not found at {path}")
            return []

        try:
            with open(path, "r") as f:
                data = json.load(f)
            categories = data.get("categories", [])
            logger.info(f"Loaded {len(categories)} categories from config")
            return categories
        except Exception as e:
            logger.error(f"Error loading categories config: {e}")
            return []

    def _validate_category(self, category: str, subcategory: str) -> None:
        """Validate category against the catalog (categories are catalog-
        only) and subcategory against catalog-then-custom. The custom-
        table lookup only fires when the catalog doesn't satisfy the
        request, keeping reclassify off the DB for curated values.

        Raises:
            ValueError: If category or subcategory is not valid
        """
        catalog_entry = next(
            (c for c in self._catalog if c["name"] == category), None
        )
        if catalog_entry is None:
            valid = [c["name"] for c in self._catalog]
            raise ValueError(
                f"Invalid category '{category}'. Valid categories: {valid}"
            )

        if subcategory in catalog_entry["subcategories"]:
            return

        if subcategory in self.custom_subcategory_repo.get_for_category(category):
            return

        raise ValueError(
            f"Invalid subcategory '{subcategory}' for category '{category}'. "
            f"Valid subcategories: {catalog_entry['subcategories']}"
        )

    def _validate_custom_subcategory(self, category: str, name: Any) -> str:
        """Validate the new subcategory name and check it doesn't collide
        with an existing catalog entry. Returns the cleaned name.
        """
        # Categories are catalog-only — analysts cannot create new top-
        # level categories because the ML pipeline keys off them.
        catalog_entry = next(
            (c for c in self._catalog if c["name"] == category), None
        )
        if catalog_entry is None:
            valid = [c["name"] for c in self._catalog]
            raise ValueError(
                f"Invalid category '{category}'. Valid categories: {valid}"
            )

        if not isinstance(name, str):
            raise ValueError("Subcategory name must be a string")

        cleaned = name.strip()
        if not cleaned:
            raise ValueError("Subcategory name must not be empty")

        if len(cleaned) > _MAX_SUBCATEGORY_LEN:
            raise ValueError(
                f"Subcategory name must be {_MAX_SUBCATEGORY_LEN} characters or fewer"
            )

        if not _PRINTABLE_ASCII_RE.match(cleaned):
            raise ValueError(
                "Subcategory name must contain only printable ASCII characters"
            )

        # Custom-vs-custom collisions are caught by the unique index at
        # insert time; here we only need to reject catalog collisions.
        cleaned_lower = cleaned.lower()
        for sub in catalog_entry["subcategories"]:
            if sub.lower() == cleaned_lower:
                raise ValueError(
                    f"Subcategory '{cleaned}' already exists for category '{category}'"
                )
        return cleaned

    def _get_current_asset(self, asset_id: str) -> Dict[str, Any] | None:
        """Fetch current asset row from database.

        manufacturer / model / vendor_category / mac_vendor_enriched are stored
        as text[]. The features snapshot persists single-value features (used
        downstream for ML similarity), so collapse each array to its first
        element here.
        """
        query = """
            SELECT id, category, subcategory,
                   COALESCE(manufacturer[1], '') AS manufacturer,
                   COALESCE(model[1], '') AS model,
                   COALESCE(os_normalized, '') AS os_normalized,
                   COALESCE(mac_vendor_enriched[1], '') AS mac_vendor,
                   COALESCE(vendor_category[1], '') AS vendor_category
            FROM assets
            WHERE id = %s::uuid
        """
        results = self.db.execute_query(query, (asset_id,), fetch=True)
        if results:
            return results[0]
        return None

    def _update_asset(
        self, asset_id: str, category: str, subcategory: str, updated_at: datetime
    ) -> None:
        """Update the asset's category, subcategory, overridden flag, and updated_at.

        `category_is_overridden` is a best-effort cache: every pipeline run with
        work resets it to false, and the source of truth is
        `category_overrides.active`. It is set here only so this path leaves the
        same state behind as `_update_assets_bulk` — writing it in one and not
        the other is what made clearing a single-asset override impossible
        (#1270).
        """
        query = """
            UPDATE assets
            SET category = %s,
                subcategory = %s,
                category_is_overridden = true,
                updated_at = %s
            WHERE id = %s::uuid
        """
        self.db.execute_query(query, (category, subcategory, updated_at, asset_id), fetch=False)

    def _fetch_current_categories(self, asset_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Snapshot current category/subcategory per asset for chronicle old-values.
        Both *_predicted columns are carried too so clear_overrides_bulk can record
        the values the live rows are actually reset to.

        `category_is_overridden` is deliberately absent: it used to be selected so
        clear_overrides_bulk could skip never-overridden assets, which is exactly
        the read that made clearing a no-op (#1270). Whether an asset carries an
        override is `override_repo.get_active_override_ids`' question now."""
        query = """
            SELECT id, category, subcategory,
                   category_predicted, subcategory_predicted
            FROM assets
            WHERE id = ANY(%(ids)s::uuid[])
        """
        rows = self.db.execute_query(query, {'ids': [str(a) for a in asset_ids]}, fetch=True)
        return {
            str(r['id']): {
                "category": r.get('category'),
                "subcategory": r.get('subcategory'),
                "category_predicted": r.get('category_predicted'),
                "subcategory_predicted": r.get('subcategory_predicted'),
            }
            for r in (rows or [])
        }

    def _update_assets_bulk(
        self, asset_ids: List[str], category: str, subcategory: str, updated_at: datetime
    ) -> None:
        """Set category/subcategory + overridden flag on the live rows (transient;
        pipeline re-applies from category_overrides next run). Category stored
        as-passed to match single-asset _update_asset.

        Same best-effort cache as `_update_asset`'s flag — nothing may key a
        decision on it."""
        query = """
            UPDATE assets
            SET category = %(category)s,
                subcategory = %(subcategory)s,
                category_is_overridden = true,
                updated_at = %(updated_at)s
            WHERE id = ANY(%(ids)s::uuid[])
        """
        self.db.execute_query(query, {
            'category': category,
            'subcategory': subcategory,
            'updated_at': updated_at,
            'ids': [str(a) for a in asset_ids],
        }, fetch=False)

    def _reset_assets_bulk(self, asset_ids: List[str], updated_at: datetime) -> None:
        """Live reset to what the pipeline predicted, for both fields.

        The COALESCE is not defensive dressing — every row written before migration
        143 has a NULL snapshot, because bulk_upsert_assets never persisted
        category_predicted at all and subcategory_predicted did not exist. On those
        rows the prediction is unrecoverable, so the statement must degrade to
        "leave the value alone"; the previous `SET category = category_predicted,
        subcategory = NULL` blanked both fields on every asset instead (#1154).
        Neither field can be NULLed from this path any more.

        The EXISTS guard ensures a never-overridden asset is never touched even if
        its id is passed in — its real subcategory must survive. It deliberately
        does NOT filter on `co.active`: the caller deactivates before calling
        this, in the same transaction, so an activity-reading guard would see
        every row it cares about already false and match nothing — the #1270 bug,
        restored, and invisible to any test with a mocked DB. An asset with an
        override row at all, active or not, is one the pipeline has classified;
        that is the property being asserted. It replaces a
        `category_is_overridden = true` guard, which read false on every override
        older than one pipeline run.

        Cheap: `category_overrides.asset_id` is the PK and `assets` is already
        narrowed by `id = ANY(...)`."""
        query = """
            UPDATE assets
            SET category = COALESCE(category_predicted, category),
                subcategory = COALESCE(subcategory_predicted, subcategory),
                category_is_overridden = false,
                updated_at = %(updated_at)s
            WHERE id = ANY(%(ids)s::uuid[])
              AND EXISTS (
                  SELECT 1 FROM category_overrides co WHERE co.asset_id = assets.id
              )
        """
        self.db.execute_query(query, {
            'updated_at': updated_at,
            'ids': [str(a) for a in asset_ids],
        }, fetch=False)

