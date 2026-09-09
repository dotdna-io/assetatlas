# SPDX-License-Identifier: AGPL-3.0-or-later
"""Orchestration for Unified Fields: validate against the live catalog, infer
the (function-aware) data_type, generate a stable slug, persist, and bust the
catalog cache. Also powers the live preview over a sample of assets."""

import logging
import uuid
from typing import Any, Dict, List, Optional

from src.modules.shared.exceptions import ValidationError, NotFoundError, ConflictError
from src.modules.shared import asset_data_version
from src.modules.fields.encoding import encode, decode
from src.modules.fields.models import VirtualField, InvalidFieldError
from src.modules.fields.types import FieldDataType
from src.modules.fields.unified.definition import (
    slugify, infer_data_type, validate_compatibility,
)
from src.modules.fields.catalog import VIRTUAL_FIELDS
from src.modules.fields.unified.materialize import (
    outside_request_scope, refresh_unified_values, remove_unified_value,
)
from src.modules.fields.unified.merge_sql import build_merge_sql

logger = logging.getLogger(__name__)

_LABEL_KEY = "virtual:asset_label"


class UnifiedFieldsService:
    def __init__(self, repo, catalog, resolver, db_adapter):
        self.repo = repo
        self.catalog = catalog
        self.resolver = resolver
        self.db = db_adapter

    # ---------- read ----------
    def list(self) -> List[Dict[str, Any]]:
        return [self._to_out(r) for r in self.repo.list_all()]

    def get(self, field_id: str) -> Dict[str, Any]:
        self._require_uuid(field_id)
        row = self.repo.get(field_id)
        if not row:
            raise NotFoundError(f"Unified field {field_id} not found")
        return self._to_out(row)

    # ---------- validation ----------
    def _resolve_source_types(self, sources: List[str]) -> List[FieldDataType]:
        types: List[FieldDataType] = []
        for key in sources:
            try:
                fid = decode(key)
            except ValueError:
                raise ValidationError(f"Invalid source field key: {key}")
            if isinstance(fid, VirtualField):
                raise ValidationError(
                    "Virtual/unified fields cannot be used as sources")
            try:
                rf = self.resolver.resolve(fid)
            except InvalidFieldError:
                raise ValidationError(f"Source field is not in the catalog: {key}")
            types.append(rf.data_type)
        return types

    def _validate(self, function: str, sources: List[str]) -> FieldDataType:
        source_types = self._resolve_source_types(sources)
        err = validate_compatibility(function, source_types)
        if err:
            raise ValidationError(err)
        dt = infer_data_type(function, source_types)
        if dt is None:
            raise ValidationError("Selected fields must share a compatible type")
        return dt

    def _require_uuid(self, field_id) -> None:
        try:
            uuid.UUID(str(field_id))
        except (ValueError, AttributeError, TypeError):
            raise NotFoundError(f"Unified field {field_id} not found")

    def _check_name(self, name: str, exclude_id: Optional[str] = None,
                    self_slug: Optional[str] = None) -> None:
        if self.repo.name_exists(name, exclude_id=exclude_id):
            raise ConflictError(f'A field named "{name}" already exists')
        hit = self.catalog.field_id_for_display_name(name)
        if hit is not None and not (isinstance(hit, VirtualField) and hit.name == self_slug):
            raise ConflictError(f'A field named "{name}" already exists')

    def _unique_slug(self, name: str) -> str:
        base = slugify(name)
        slug, n = base, 2
        while self.repo.slug_exists(slug) or slug in VIRTUAL_FIELDS:
            slug, n = f"{base}_{n}", n + 1
        return slug

    # ---------- write ----------
    def create(self, data) -> Dict[str, Any]:
        dt = self._validate(data.function, data.sources)
        self._check_name(data.name)
        slug = self._unique_slug(data.name)
        row = self.repo.create(slug=slug, name=data.name, function=data.function,
                               sources=data.sources, data_type=dt.value)
        self.catalog.invalidate()
        self._refresh_materialized([slug])
        # A new unified field introduces a new set of distinct values —
        # invalidate the Explorer field-values cache (#1410).
        asset_data_version.bump()
        return self._to_out(row)

    def update(self, field_id: str, data) -> Dict[str, Any]:
        self._require_uuid(field_id)
        existing = self.repo.get(field_id)
        if not existing:
            raise NotFoundError(f"Unified field {field_id} not found")
        dt = self._validate(data.function, data.sources)
        self._check_name(data.name, exclude_id=field_id, self_slug=existing["slug"])
        # Clear the stamp BEFORE the definition update: while the stamp is set
        # the resolver trusts stored values, which were built from the OLD
        # definition. Cleared, it falls back to the live merge expression
        # until the refresh below re-stamps. The clear and the UPDATE commit
        # together — at the scope exit inside _refresh_materialized.
        self._clear_materialized_stamp([existing["slug"]])
        row = self.repo.update(field_id, name=data.name, function=data.function,
                               sources=data.sources, data_type=dt.value)
        if row is None:                      # concurrent delete between get() and update()
            raise NotFoundError(f"Unified field {field_id} not found")
        self.catalog.invalidate()
        self._refresh_materialized(
            [row["slug"]],
            # Defensive: repo.update never writes slug (slugs are immutable on
            # update), so this is always None today — but if an update ever
            # could rename a slug, the old key's stored value must be dropped.
            remove_slug=existing["slug"] if row["slug"] != existing["slug"] else None,
        )
        # The re-materialized field's values were just rebuilt from the new
        # definition — invalidate the Explorer field-values cache (#1410).
        asset_data_version.bump()
        return self._to_out(row)

    def delete(self, field_id: str) -> None:
        self._require_uuid(field_id)
        existing = self.repo.get(field_id)
        if not existing:
            raise NotFoundError(f"Unified field {field_id} not found")
        if not self.repo.delete(field_id):
            raise NotFoundError(f"Unified field {field_id} not found")
        self.catalog.invalidate()
        self._remove_materialized_value(existing["slug"])
        # The deleted field's values are gone — invalidate the Explorer
        # field-values cache (#1410).
        asset_data_version.bump()

    # ---------- materialized-value maintenance ----------
    # Best-effort, all three: a hook failure must never fail the CRUD action.
    # The failure mode is a cleared materialized_at stamp — the resolver falls
    # back to the live merge expression (correct, just slower) and the next
    # pipeline run re-materializes and re-stamps. Each hook runs inside
    # outside_request_scope so the CRUD write above commits FIRST: a hook's
    # DB-level failure then cannot roll the user's write back (it could only
    # leave stamps cleared — the mode above), and the failure-path stamp
    # clear is itself durable.

    def _refresh_materialized(self, slugs: List[str],
                              remove_slug: Optional[str] = None) -> None:
        with outside_request_scope(self.db.conn_manager):
            try:
                if remove_slug is not None:
                    remove_unified_value(self.db, remove_slug)
                refresh_unified_values(
                    self.db, self.resolver, self.repo.list_for_catalog(),
                    slugs=list(slugs))
            except Exception:
                logger.warning(
                    "materialized refresh failed for unified field(s) %s — leaving "
                    "materialized_at cleared (live SQL until the next pipeline run)",
                    list(slugs), exc_info=True)
                self._clear_materialized_stamp(slugs)

    def _clear_materialized_stamp(self, slugs: List[str]) -> None:
        try:
            self.repo.set_materialized_at(list(slugs), False)
        except Exception:
            logger.warning(
                "could not clear materialized_at for unified field(s) %s",
                list(slugs), exc_info=True)

    def _remove_materialized_value(self, slug: str) -> None:
        with outside_request_scope(self.db.conn_manager):
            try:
                remove_unified_value(self.db, slug)
            except Exception:
                logger.warning(
                    "could not drop the stored value of deleted unified field %r — "
                    "the next pipeline run removes it", slug, exc_info=True)

    # ---------- preview ----------
    def preview(self, data) -> Dict[str, Any]:
        dt = self._validate(data.function, data.sources)
        merge_sql = build_merge_sql(self.resolver, data.function, data.sources)
        source_cols = [self.resolver.resolve(decode(k)).select_expr(alias=k)
                       for k in data.sources]
        label = self.resolver.resolve(decode(_LABEL_KEY)).select_expr(alias="__hostname__")
        select = ", ".join([
            "id::text AS asset_id",
            label,
            *source_cols,
            f"{merge_sql} AS result",
        ])
        sql = (f"SELECT {select} FROM assets "
               f"ORDER BY ({merge_sql} IS NULL) LIMIT %(lim)s")
        rows = self.db.execute_query(sql, {"lim": data.sample_size}) or []
        out_rows = [{
            "asset_id": r["asset_id"],
            "hostname": r.get("__hostname__"),
            "source_values": {k: r.get(k) for k in data.sources},
            "result": r.get("result"),
        } for r in rows]
        return {"data_type": dt.value, "rows": out_rows}

    # ---------- serialization ----------
    def _to_out(self, row: Dict[str, Any]) -> Dict[str, Any]:
        created = row.get("created_at")
        updated = row.get("updated_at")
        return {
            "id": row["id"],
            "name": row["name"],
            "function": row["function"],
            "sources": row["sources"],
            "data_type": row["data_type"],
            "encoded_key": encode(VirtualField(kind="virtual", name=row["slug"])),
            "created_at": created.isoformat() if created else None,
            "updated_at": updated.isoformat() if updated else None,
        }
