# SPDX-License-Identifier: AGPL-3.0-or-later
"""
ConfigSchema Builder

Provides a typed builder for connector config schemas.
Produces the same dict format that the registry, wizard UI, and
BaseConnector.validate_config() already consume.
"""
from typing import Any, Dict, List, Optional


class ConfigSchema:
    """Builder for connector configuration schemas.

    Usage:
        schema = ConfigSchema()
        schema.add_url("base_url", "API Base URL", placeholder="https://...")
        schema.add_password("api_key", "API Key")
        schema.add_number("page_size", "Page Size", default=100, min_val=1, max_val=1000)
        return schema.build()
    """

    def __init__(self):
        self._fields: Dict[str, Dict[str, Any]] = {}

    def add_url(self, name: str, label: str, *, required: bool = True,
                placeholder: Optional[str] = None, description: Optional[str] = None) -> "ConfigSchema":
        self._fields[name] = self._base_field("url", label, required, description)
        if placeholder:
            self._fields[name]["placeholder"] = placeholder
        return self

    def add_string(self, name: str, label: str, *, required: bool = True,
                   placeholder: Optional[str] = None, description: Optional[str] = None) -> "ConfigSchema":
        self._fields[name] = self._base_field("string", label, required, description)
        if placeholder:
            self._fields[name]["placeholder"] = placeholder
        return self

    def add_password(self, name: str, label: str, *, required: bool = True,
                     description: Optional[str] = None) -> "ConfigSchema":
        field = self._base_field("password", label, required, description)
        field["sensitive"] = True
        self._fields[name] = field
        return self

    def add_number(self, name: str, label: str, *, required: bool = False,
                   default: Optional[Any] = None, min_val: Optional[int] = None,
                   max_val: Optional[int] = None, description: Optional[str] = None) -> "ConfigSchema":
        field = self._base_field("number", label, required, description)
        if default is not None:
            field["default"] = default
        if min_val is not None or max_val is not None:
            validation = {}
            if min_val is not None:
                validation["min"] = min_val
            if max_val is not None:
                validation["max"] = max_val
            field["validation"] = validation
        self._fields[name] = field
        return self

    def add_select(self, name: str, label: str, options: List[Dict[str, str]], *,
                   default: Optional[str] = None, required: bool = False,
                   description: Optional[str] = None) -> "ConfigSchema":
        field = self._base_field("select", label, required, description)
        field["options"] = options
        if default is not None:
            field["default"] = default
        self._fields[name] = field
        return self

    def add_boolean(self, name: str, label: str, *, default: bool = False,
                    required: bool = False, description: Optional[str] = None) -> "ConfigSchema":
        field = self._base_field("boolean", label, required, description)
        field["default"] = default
        self._fields[name] = field
        return self

    def build(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._fields)

    @staticmethod
    def _base_field(field_type: str, label: str, required: bool,
                    description: Optional[str]) -> Dict[str, Any]:
        field: Dict[str, Any] = {
            "type": field_type,
            "label": label,
            "required": required,
        }
        if description:
            field["description"] = description
        return field
