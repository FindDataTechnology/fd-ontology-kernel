"""Error codes are a closed, named set — assert the closure mechanically."""

from __future__ import annotations

import pytest

from fd_ontology_kernel import errors


def test_every_code_is_unique_and_documented() -> None:
    values = [code.value for code in errors.ErrorCode]
    assert len(values) == len(set(values))
    assert errors.ErrorCode.KEY_UNFOLDED.value == "KEY_UNFOLDED"


def test_every_exception_maps_to_its_code() -> None:
    for cls in errors.BY_CODE.values():
        instance = cls("boom")
        assert instance.code in errors.ErrorCode
        assert instance.code.value in str(instance)
        assert isinstance(instance, errors.OntologyKernelError)


def test_by_code_covers_every_error_class_defined_here() -> None:
    defined = {
        obj for obj in vars(errors).values()
        if isinstance(obj, type)
        and issubclass(obj, errors.OntologyKernelError)
        and obj is not errors.OntologyKernelError
    }
    assert defined == set(errors.BY_CODE.values())


def test_context_is_structured_not_stringly() -> None:
    exc = errors.VocabOutOfSet("bad kind", set_name="entity_types", value="penalty")
    assert exc.context["set_name"] == "entity_types"
    assert exc.context["value"] == "penalty"


def test_codes_are_catchable_by_code() -> None:
    with pytest.raises(errors.OntologyKernelError) as caught:
        raise errors.ShapeUnknown("nope", requested=9)
    assert caught.value.code is errors.ErrorCode.MANIFEST_SHAPE_UNKNOWN
