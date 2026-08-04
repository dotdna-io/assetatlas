# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from pydantic import ValidationError
from src.modules.fields.unified.models import UnifiedFieldWrite, UnifiedFieldPreviewRequest


def test_write_model_valid():
    m = UnifiedFieldWrite(name="lentity", function="coalesce",
                          sources=["ext:A:lentity", "ext:B:lentity"])
    assert m.function == "coalesce"
    assert m.sources == ["ext:A:lentity", "ext:B:lentity"]


def test_write_model_rejects_bad_function():
    with pytest.raises(ValidationError):
        UnifiedFieldWrite(name="x", function="sum", sources=["a", "b"])


def test_write_model_requires_two_sources():
    with pytest.raises(ValidationError):
        UnifiedFieldWrite(name="x", function="coalesce", sources=["a"])


def test_preview_defaults_sample_size():
    m = UnifiedFieldPreviewRequest(function="union", sources=["a", "b"])
    assert m.sample_size == 10
