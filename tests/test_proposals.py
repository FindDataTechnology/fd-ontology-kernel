"""Proposals: claim identity, submitters, rejection, and batch results."""

from __future__ import annotations

import pytest

from fd_ontology_kernel import proposals as proposals_mod
from fd_ontology_kernel.errors import InvariantViolated, ProposalStaleEvidence
from fd_ontology_kernel.memory import (
    MemoryJournal,
    MemoryProposalBox,
    MemoryPublicationStore,
    StaticVocabulary,
    memory_kernel,
    memory_vocabulary,
)

EVIDENCE = {"source": "chunk-1", "quote": "合同应当采用书面形式"}


def _box() -> MemoryProposalBox:
    return MemoryProposalBox()


def test_first_submission_creates_a_pending_proposal() -> None:
    box = _box()
    proposal, created = proposals_mod.submit(
        box, scope_id="d1", source="fleet:scout", kind="law", external_key="合同法",
        body="合同法", evidence=EVIDENCE, submitted_by="scout",
    )
    assert created is True
    assert proposal.state == "pending"
    assert proposal.proposal_id == 1


def test_same_claim_from_two_posts_is_one_row_with_both_submitters() -> None:
    box = _box()
    first, created_first = proposals_mod.submit(
        box, scope_id="d1", source="fleet:scout", kind="law", external_key="合同法",
        body="合同法", evidence=EVIDENCE, submitted_by="scout",
    )
    second, created_second = proposals_mod.submit(
        box, scope_id="d1", source="app:analyst", kind="law", external_key="合同法",
        body="合同法", evidence=EVIDENCE, submitted_by="analyst",
    )
    assert created_first is True and created_second is False
    assert second.proposal_id == first.proposal_id
    assert box.count() == 1
    assert second.submitters == ("fleet:scout", "app:analyst")


def test_different_evidence_is_a_different_claim() -> None:
    box = _box()
    proposals_mod.submit(box, scope_id="d1", source="s1", kind="law", external_key="合同法",
                         body="b", evidence=EVIDENCE, submitted_by="s1")
    proposals_mod.submit(box, scope_id="d1", source="s2", kind="law", external_key="合同法",
                         body="b", evidence={"source": "chunk-2", "quote": "另一句"}, submitted_by="s2")
    assert box.count() == 2


def test_resubmitting_rejected_evidence_is_refused() -> None:
    box = _box()
    proposal, _ = proposals_mod.submit(
        box, scope_id="d1", source="s1", kind="law", external_key="合同法",
        body="b", evidence=EVIDENCE, submitted_by="s1",
    )
    proposals_mod.reject(box, scope_id="d1", proposal_id=proposal.proposal_id, decided_by="lead")
    with pytest.raises(ProposalStaleEvidence):
        proposals_mod.submit(
            box, scope_id="d1", source="s1", kind="law", external_key="合同法",
            body="b", evidence=EVIDENCE, submitted_by="s1",
        )


def test_resubmitting_with_new_evidence_after_rejection_is_allowed() -> None:
    box = _box()
    proposal, _ = proposals_mod.submit(
        box, scope_id="d1", source="s1", kind="law", external_key="合同法",
        body="b", evidence=EVIDENCE, submitted_by="s1",
    )
    proposals_mod.reject(box, scope_id="d1", proposal_id=proposal.proposal_id, decided_by="lead")
    fresh, created = proposals_mod.submit(
        box, scope_id="d1", source="s1", kind="law", external_key="合同法",
        body="b", evidence={"source": "chunk-9", "quote": "新证据"}, submitted_by="s1",
    )
    assert created is True and fresh.state == "pending"


def test_evidence_without_source_or_quote_is_refused() -> None:
    box = _box()
    with pytest.raises(InvariantViolated):
        proposals_mod.submit(box, scope_id="d1", source="s", kind="law",
                             external_key="k", body="b", evidence={"quote": "q"}, submitted_by="s")
    with pytest.raises(InvariantViolated):
        proposals_mod.submit(box, scope_id="d1", source="s", kind="law",
                             external_key="k", body="b", evidence={"source": "c"}, submitted_by="s")


def test_confirm_writes_through_the_kernel_and_marks_the_proposal() -> None:
    kernel = memory_kernel()
    proposal, _ = kernel.propose(
        scope_id="d1", source="fleet:scout", kind="law", external_key="合同法",
        body="合同法", evidence=EVIDENCE, submitted_by="scout",
    )
    result = kernel.confirm(scope_id="d1", proposal_id=proposal.proposal_id, decided_by="lead")
    assert result["result"] == "confirmed"
    assert kernel.units.get(scope_id="d1", kind="law", external_key="合同法") is not None
    assert [e.op for e in kernel.events.fold(scope_id="d1")] == ["create"]


def test_relationship_proposal_carries_endpoints_into_the_write() -> None:
    kernel = memory_kernel()
    kernel.write(scope_id="d1", kind="concept", external_key="违约责任", body="违约责任", actor="u")
    kernel.write(scope_id="d1", kind="law", external_key="合同法", body="合同法", actor="u")
    proposal, _ = kernel.propose(
        scope_id="d1", source="fleet:scout", kind="defines",
        external_key="违约责任->defines:合同法", body="违约责任 defines 合同法",
        evidence={**EVIDENCE, "from_key": "违约责任", "to_key": "合同法"},
        submitted_by="scout",
    )
    kernel.confirm(scope_id="d1", proposal_id=proposal.proposal_id, decided_by="lead")
    stored = kernel.units.get(
        scope_id="d1", kind="defines", external_key="违约责任->defines:合同法"
    )
    assert stored.attrs["from_key"] == "违约责任"
    assert stored.attrs["to_key"] == "合同法"


def test_confirming_twice_is_refused_without_writing_again() -> None:
    kernel = memory_kernel()
    proposal, _ = kernel.propose(
        scope_id="d1", source="s", kind="law", external_key="合同法",
        body="合同法", evidence=EVIDENCE, submitted_by="s",
    )
    kernel.confirm(scope_id="d1", proposal_id=proposal.proposal_id, decided_by="lead")
    with pytest.raises(InvariantViolated):
        kernel.confirm(scope_id="d1", proposal_id=proposal.proposal_id, decided_by="lead")
    assert len(kernel.events.fold(scope_id="d1")) == 1


def test_batch_confirm_reports_each_item_and_does_not_roll_back() -> None:
    kernel = memory_kernel()
    good, _ = kernel.propose(
        scope_id="d1", source="s", kind="law", external_key="合同法",
        body="b", evidence={**EVIDENCE, "quote": "甲"}, submitted_by="s",
    )
    other, _ = kernel.propose(
        scope_id="d1", source="s", kind="law", external_key="劳动法",
        body="b", evidence={**EVIDENCE, "quote": "乙"}, submitted_by="s",
    )
    kernel.confirm(scope_id="d1", proposal_id=other.proposal_id, decided_by="lead")

    result = proposals_mod.confirm_batch(
        kernel, kernel.proposals, scope_id="d1",
        proposal_ids=[good.proposal_id, other.proposal_id], decided_by="lead",
    )
    assert result["confirmed"] == 1 and result["failed"] == 1
    assert kernel.units.get(scope_id="d1", kind="law", external_key="合同法") is not None
    failure = [r for r in result["results"] if r["result"] == "failed"][0]
    assert failure["proposal_id"] == other.proposal_id
    assert "InvariantViolated" in failure["error"]


def test_proposal_does_not_touch_the_ontology_until_confirmed() -> None:
    kernel = memory_kernel()
    kernel.propose(scope_id="d1", source="s", kind="law", external_key="合同法",
                   body="b", evidence=EVIDENCE, submitted_by="s")
    assert kernel.units.list_live(scope_id="d1") == []
    assert kernel.events.fold(scope_id="d1") == []
