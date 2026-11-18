"""Contracts: frozen shapes, envelope discipline, and event validation."""

from __future__ import annotations

import dataclasses

import pytest

from fd_ontology_kernel.contracts import (
    Event,
    ManifestEnvelope,
    Proposal,
    Relationship,
    Scope,
    Unit,
    relationship_key,
)


def test_unit_is_frozen() -> None:
    unit = Unit(scope_id="d", kind="law", external_key="合同法")
    with pytest.raises(dataclasses.FrozenInstanceError):
        unit.body = "changed"  # type: ignore[misc]


def test_event_is_frozen() -> None:
    event = Event(scope_id="d", op="create", target_kind="law", target_key="合同法",
                  actor="u", via="test")
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.after = {"body": "rewritten"}  # type: ignore[misc]


def test_event_refuses_a_before_state_on_create() -> None:
    with pytest.raises(ValueError):
        Event(scope_id="d", op="create", target_kind="law", target_key="k",
              actor="u", via="t", before={"body": "old"})


def test_event_refuses_an_after_state_on_delete() -> None:
    with pytest.raises(ValueError):
        Event(scope_id="d", op="delete", target_kind="law", target_key="k",
              actor="u", via="t", after={"body": "new"})


def test_event_needs_a_portable_key_not_just_an_id() -> None:
    with pytest.raises(ValueError):
        Event(scope_id="d", op="create", target_kind="law", target_key="",
              actor="u", via="t")


def test_envelope_keeps_the_shape_version_outside_the_payload() -> None:
    envelope = ManifestEnvelope(manifest_schema_version=2, payload={"entities": []})
    assert "manifest_schema_version" not in envelope.payload
    assert envelope.as_dict()["manifest_schema_version"] == 2


def test_relationship_key_shape_is_the_published_convention() -> None:
    assert relationship_key(from_key="订单", kind="belongs_to", to_key="客户") == (
        "订单->belongs_to:客户"
    )


def test_relationship_projects_to_a_unit_with_endpoints_in_attrs() -> None:
    rel = Relationship(scope_id="d", kind="belongs_to", from_key="订单", to_key="客户")
    unit = rel.to_unit()
    assert unit.external_key == "订单->belongs_to:客户"
    assert unit.attrs["from_key"] == "订单"
    assert unit.attrs["to_key"] == "客户"


def test_proposal_submitters_are_deduped_in_order() -> None:
    proposal = Proposal(
        scope_id="d", source="post-a", kind="law", external_key="k", body="b",
        evidence={"source": "chunk", "quote": "q"}, evidence_digest="x",
        submitted_by="a", sources=("post-b", "post-a", "post-c"),
    )
    assert proposal.submitters == ("post-a", "post-b", "post-c")


def test_scope_needs_an_id() -> None:
    with pytest.raises(ValueError):
        Scope(id="")


def test_unit_with_key_preserves_everything_else() -> None:
    unit = Unit(scope_id="d", kind="law", external_key="raw", body="b", tier="bronze")
    moved = unit.with_key("folded", aliases=["raw"])
    assert moved.external_key == "folded"
    assert moved.body == "b" and moved.tier == "bronze"
    assert moved.attrs["aliases"] == ["raw"]
