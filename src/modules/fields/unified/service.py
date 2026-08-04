# SPDX-License-Identifier: AGPL-3.0-or-later
"""Orchestration for Unified Fields: validate against the live catalog, infer
the (function-aware) data_type, generate a stable slug, persist, and bust the
catalog cache. Also powers the live preview over a sample of assets."""

import uuid
from typing import Any, Dict, List, Optional

from src.api.middleware.error_handler import ValidationError, NotFoundError, ConflictError
from src.modules.fields.encoding import encode, decode
from src.modules.fields.models import VirtualField, InvalidFieldError
from src.modules.fields.types import FieldDataType
from src.modules.fields.unified.definition import (
    slugify, infer_data_type, validate_compatibility,
)
from src.modules.fields.catalog import VIRTUAL_FIELDS
from src.modules.fields.unified.merge_sql import build_merge_sql

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
        return self._to_out(row)

    def update(self, field_id: str, data) -> Dict[str, Any]:
        self._require_uuid(field_id)
        existing = self.repo.get(field_id)
        if not existing:
            raise NotFoundError(f"Unified field {field_id} not found")
        dt = self._validate(data.function, data.sources)
        self._check_name(data.name, exclude_id=field_id, self_slug=existing["slug"])
        row = self.repo.update(field_id, name=data.name, function=data.function,
                               sources=data.sources, data_type=dt.value)
        if row is None:                      # concurrent delete between get() and update()
            raise NotFoundError(f"Unified field {field_id} not found")
        self.catalog.invalidate()
        return self._to_out(row)

    def delete(self, field_id: str) -> None:
        self._require_uuid(field_id)
        if not self.repo.delete(field_id):
            raise NotFoundError(f"Unified field {field_id} not found")
        self.catalog.invalidate()

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
