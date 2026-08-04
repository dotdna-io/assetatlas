# SPDX-License-Identifier: AGPL-3.0-or-later
from src.modules.fields.types import FieldDataType as DT
from src.modules.fields.unified.definition import (
    slugify, type_class, functions_for_types, infer_data_type, validate_compatibility,
)


def test_slugify_basics():
    assert slugify("Legal Entity") == "legal_entity"
    assert slugify("  lentity  ") == "lentity"
    assert slugify("A/B-C") == "a_b_c"
    assert slugify("!!!") == "field"  # fallback when nothing survives


def test_type_class():
    assert type_class(DT.STRING) == "text"
    assert type_class(DT.STRING_ARRAY) == "text"
    assert type_class(DT.JSONB_ARRAY) == "text"
    assert type_class(DT.INTEGER) == "number"
    assert type_class(DT.TIMESTAMP) == "date"
    assert type_class(DT.BOOLEAN) == "other"


def test_functions_for_types():
    assert functions_for_types([DT.STRING, DT.STRING]) == ["coalesce", "union"]
    assert functions_for_types([DT.TIMESTAMP, DT.DATE]) == ["coalesce", "union", "max", "min"]
    assert functions_for_types([DT.STRING, DT.INTEGER]) == []  # mixed class


def test_infer_data_type_is_function_aware():
    # union always yields an array regardless of source class
    assert infer_data_type("union", [DT.STRING, DT.STRING]) == DT.STRING_ARRAY
    assert infer_data_type("union", [DT.TIMESTAMP, DT.TIMESTAMP]) == DT.STRING_ARRAY
    # coalesce/max/min -> scalar of the common class
    assert infer_data_type("coalesce", [DT.STRING, DT.STRING]) == DT.STRING
    assert infer_data_type("coalesce", [DT.INTEGER, DT.INTEGER]) == DT.INTEGER
    assert infer_data_type("coalesce", [DT.INTEGER, DT.FLOAT]) == DT.FLOAT
    assert infer_data_type("max", [DT.DATE, DT.TIMESTAMP]) == DT.TIMESTAMP
    assert infer_data_type("coalesce", [DT.STRING, DT.INTEGER]) is None
    # invalid function/type combos should return None
    assert infer_data_type("max", [DT.STRING, DT.STRING]) is None
    assert infer_data_type("min", [DT.STRING, DT.STRING]) is None


def test_validate_compatibility():
    # happy paths
    assert validate_compatibility("coalesce", [DT.STRING, DT.STRING]) is None
    assert validate_compatibility("union", [DT.STRING, DT.STRING_ARRAY]) is None
    assert validate_compatibility("max", [DT.TIMESTAMP, DT.TIMESTAMP]) is None
    # too few
    assert "two source" in validate_compatibility("coalesce", [DT.STRING])
    # mixed class
    assert "compatible type" in validate_compatibility("coalesce", [DT.STRING, DT.INTEGER])
    # function not valid for class (max on text)
    assert "not valid" in validate_compatibility("max", [DT.STRING, DT.STRING])
    # D6: coalesce/max/min require scalar sources -> array text source rejected
    assert "single-value" in validate_compatibility("coalesce", [DT.STRING, DT.STRING_ARRAY])
