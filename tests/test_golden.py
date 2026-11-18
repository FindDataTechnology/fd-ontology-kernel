"""Golden digests captured from the two live implementations.

Each fixture was produced by running the surface's *own* code on a fixed payload
(see the ``source`` field), not by running this kernel. The assertion is
therefore cross-implementation: if the kernel's normalizer or canonical-JSON
recipe drifts by a single byte, these fail.

Why this matters more than a unit test: the digest is a published contract. A
change that silently alters it invalidates every exported snapshot and every
version a consumer has pinned, and nothing else in the suite would notice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fd_ontology_kernel import manifest

GOLDEN = Path(__file__).parent / "golden"


def _cases(shape_file: str) -> list[dict]:
    data = json.loads((GOLDEN / shape_file).read_text(encoding="utf-8"))
    assert data["cases"], f"{shape_file} must carry at least one case"
    return data["cases"]


@pytest.mark.parametrize("case", _cases("shape1.json"), ids=lambda c: c["name"])
def test_shape1_reproduces_the_customer_surface_digest(case: dict) -> None:
    produced = manifest.digest_for(shape=1, payload=case["payload"])
    assert produced == case["digest"]


@pytest.mark.parametrize("case", _cases("shape2.json"), ids=lambda c: c["name"])
def test_shape2_reproduces_the_domain_surface_digest(case: dict) -> None:
    produced = manifest.digest_for(shape=2, payload=case["payload"])
    assert produced == case["digest"]


def test_both_shapes_have_three_captured_cases() -> None:
    assert len(_cases("shape1.json")) >= 3
    assert len(_cases("shape2.json")) >= 3


def test_a_single_byte_change_breaks_the_golden_digest() -> None:
    """The fixture is load-bearing: prove it notices a one-character edit."""
    case = _cases("shape2.json")[0]
    payload = json.loads(json.dumps(case["payload"]))
    payload["entities"][0]["body"] = payload["entities"][0]["body"] + "。"
    assert manifest.digest_for(shape=2, payload=payload) != case["digest"]


def test_shape_swap_is_detected_not_silently_accepted() -> None:
    """A shape-1 payload digested as shape 2 must not match the shape-1 digest."""
    case = _cases("shape1.json")[0]
    as_shape2 = manifest.digest_for(shape=2, payload=case["payload"])
    assert as_shape2 != case["digest"]
