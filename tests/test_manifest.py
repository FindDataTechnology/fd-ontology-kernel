"""Manifests: canonical JSON, content-only digests, shape versions, diffs."""

from __future__ import annotations

import hashlib
import json

import pytest

from fd_ontology_kernel import manifest
from fd_ontology_kernel.errors import ShapeUnknown

WIRE_ENTITIES = [
    {
        "id": 2,
        "name": "订单",
        "dataset_id": "ds-2",
        "pk_columns": ["order_id"],
        "description": "订单表",
        "display_column": "order_id",
        "column_notes": {"amount": "元"},
        "tier": "silver",
        "sensitivity": "internal",
    },
    {
        "id": 1,
        "name": "客户",
        "dataset_id": "ds-1",
        "pk_columns": ["customer_id"],
        "description": None,
        "display_column": None,
        "column_notes": None,
        "tier": None,
        "sensitivity": None,
    },
]

WIRE_RELATIONSHIPS = [
    {
        "id": 10,
        "from_entity_id": 2,
        "from_column": "customer_id",
        "to_entity_id": 1,
        "to_column": "customer_id",
        "kind": "n:1",
        "declared_kind": "n:1",
        "label": None,
        "junction_dataset_id": None,
        "junction_source_column": None,
        "junction_target_column": None,
        "tier": None,
        "sensitivity": None,
    }
]

DOMAIN_PAYLOAD = {
    "domain": "legal-smoke",
    "template_name": "legal",
    "template_version": 3,
    "entity_types": ["law", "clause", "concept"],
    "relationship_kinds": ["cites_law", "defines"],
    "entities": [
        {"kind": "law", "external_key": "合同法", "body": "合同法", "tier": "raw",
         "sensitivity": None, "attrs": {"aliases": ["中华人民共和国合同法"]}},
        {"kind": "clause", "external_key": "合同法#第12条", "body": "第十二条", "tier": "raw",
         "sensitivity": None, "attrs": {}},
    ],
    "relationships": [
        {"kind": "defines", "external_key": "违约责任->defines:合同法#第12条",
         "body": "违约责任 defines 合同法#第12条", "tier": "raw", "sensitivity": None,
         "attrs": {"from_key": "违约责任", "to_key": "合同法#第12条"},
         "from_key": "违约责任", "to_key": "合同法#第12条"},
    ],
}


def _expected_digest(payload: dict) -> str:
    """The recipe, written out independently of manifest.py."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_digest_is_order_insensitive_in_both_shapes() -> None:
    forward = manifest.digest_for(
        shape=1, payload={"entities": WIRE_ENTITIES, "relationships": WIRE_RELATIONSHIPS}
    )
    reversed_payload = {
        "entities": list(reversed(WIRE_ENTITIES)),
        "relationships": list(reversed(WIRE_RELATIONSHIPS)),
    }
    backward = manifest.digest_for(shape=1, payload=reversed_payload)
    assert forward == backward

    a = manifest.digest_for(shape=2, payload=DOMAIN_PAYLOAD)
    shuffled = dict(DOMAIN_PAYLOAD)
    shuffled["entities"] = list(reversed(DOMAIN_PAYLOAD["entities"]))
    b = manifest.digest_for(shape=2, payload=shuffled)
    assert a == b


def test_digest_matches_an_independent_implementation() -> None:
    canonical = manifest.canonical_payload(shape=1, payload={
        "entities": WIRE_ENTITIES, "relationships": WIRE_RELATIONSHIPS,
    })
    assert manifest.digest_of(canonical) == _expected_digest(canonical)


def test_shape_1_normalization_is_pinned() -> None:
    canonical = manifest.canonical_payload(shape=1, payload={
        "entities": WIRE_ENTITIES, "relationships": WIRE_RELATIONSHIPS,
    })
    assert set(canonical) == {"entities", "relationships"}
    assert [e["id"] for e in canonical["entities"]] == [1, 2]
    assert set(canonical["entities"][0]) == {
        "id", "name", "dataset_id", "pk_columns", "description",
        "display_column", "column_notes", "tier", "sensitivity",
    }
    assert set(canonical["relationships"][0]) == {
        "id", "from_entity_id", "from_column", "to_entity_id", "to_column",
        "kind", "declared_kind", "label", "junction_dataset_id",
        "junction_source_column", "junction_target_column", "tier", "sensitivity",
    }


def test_shape_2_normalization_is_pinned() -> None:
    canonical = manifest.canonical_payload(shape=2, payload=DOMAIN_PAYLOAD)
    assert set(canonical) == {
        "domain", "template_name", "template_version", "entity_types",
        "relationship_kinds", "entities", "relationships",
    }
    assert [e["external_key"] for e in canonical["entities"]] == ["合同法", "合同法#第12条"]
    assert set(canonical["entities"][0]) == {
        "kind", "external_key", "body", "tier", "sensitivity", "attrs",
    }
    assert set(canonical["relationships"][0]) == {
        "kind", "external_key", "body", "tier", "sensitivity", "attrs",
        "from_key", "to_key",
    }


def test_unknown_shape_is_refused_not_guessed() -> None:
    with pytest.raises(ShapeUnknown) as caught:
        manifest.canonical_payload(shape=99, payload={"entities": []})
    assert caught.value.context["supported"] == [1, 2]


def test_envelope_does_not_change_the_digest() -> None:
    plain = manifest.digest_for(shape=2, payload=DOMAIN_PAYLOAD)
    enveloped = manifest.envelope(shape=2, payload=DOMAIN_PAYLOAD)
    assert manifest.digest_of(enveloped.payload) == plain
    assert enveloped.manifest_schema_version == 2


def test_empty_payload_is_recognized() -> None:
    assert manifest.is_empty({"entities": [], "relationships": []})
    assert not manifest.is_empty({"entities": [{"id": 1}], "relationships": []})


def test_diff_reports_added_removed_and_changed_by_key() -> None:
    old = manifest.canonical_payload(shape=2, payload=DOMAIN_PAYLOAD)
    new_payload = {
        **DOMAIN_PAYLOAD,
        "entities": [
            {**DOMAIN_PAYLOAD["entities"][0], "body": "中华人民共和国合同法"},
            DOMAIN_PAYLOAD["entities"][1],
            {"kind": "concept", "external_key": "违约责任", "body": "违约责任",
             "tier": "raw", "sensitivity": None, "attrs": {}},
        ],
    }
    new = manifest.canonical_payload(shape=2, payload=new_payload)
    diff = manifest.diff_manifests(old, new)
    assert [e["external_key"] for e in diff["added"]] == ["违约责任"]
    assert diff["removed"] == []
    assert diff["changed"][0]["identity"] == ["law", "合同法"]
    assert diff["changed"][0]["fields"]["body"] == ["合同法", "中华人民共和国合同法"]


def test_diff_detects_duplicate_identity_as_corruption() -> None:
    payload = manifest.canonical_payload(shape=2, payload=DOMAIN_PAYLOAD)
    duplicated = dict(payload)
    duplicated["entities"] = [payload["entities"][0], payload["entities"][0]]
    with pytest.raises(ValueError):
        manifest.diff_manifests(None, duplicated)


def test_dates_serialize_at_second_precision() -> None:
    from datetime import datetime

    payload = {"at": datetime(2026, 11, 12, 8, 30, 15, 123456)}
    assert manifest.canonical_json(payload) == '{"at":"2026-11-12T08:30:15"}'
