"""The proposal state machine: machines nominate, humans decide.

A candidate produced by a model or an agent must never reach the ontology
directly. It becomes a *proposal* carrying the evidence it rests on; only a
human decision turns it into an instance, and that write goes through the same
single write path as an authored one.

Identity is the interesting part. A pending proposal is identified by
``(scope, external_key, evidence_digest)`` — the *claim*, not the submitter.
Two posts proposing the same thing from the same evidence produce one row that
records both submitters, because a reviewer has one pair of eyes. A rejected
proposal is history, not identity: the same key may be resubmitted only with
different evidence.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Optional, Sequence

from .contracts import Proposal
from .errors import InvariantViolated, ProposalStaleEvidence
from .ports import ProposalBox


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def evidence_digest(evidence: Any) -> str:
    """Stable digest of a proposal's evidence (order-insensitive, type-stable).

    Accepts any JSON value, not just a mapping: a surface that historically
    digested a list or a string must keep producing the same digest, or every
    previously rejected proposal becomes resubmittable as "new evidence".
    """
    return hashlib.sha256(
        _canonical({} if evidence is None else evidence).encode("utf-8")
    ).hexdigest()


def validate_evidence(evidence: Mapping[str, Any]) -> None:
    """Evidence must name a source and carry a quote — nothing else is enforced here.

    Verbatim checking against the source text is the adapter's job (it owns the
    corpus and the dataset); this only refuses evidence that could not be checked
    at all.
    """
    if not isinstance(evidence, Mapping):
        raise InvariantViolated("evidence must be an object", got=type(evidence).__name__)
    if not (evidence.get("source") or evidence.get("chunk_id") or evidence.get("source_key")):
        raise InvariantViolated(
            "evidence must identify its source (source/chunk_id/source_key)",
            keys=sorted(evidence),
        )
    if not str(evidence.get("quote") or "").strip():
        raise InvariantViolated("evidence must carry a verbatim quote", keys=sorted(evidence))


def submit(
    box: ProposalBox,
    *,
    scope_id: str,
    source: str,
    kind: str,
    external_key: str,
    body: str,
    evidence: Mapping[str, Any],
    submitted_by: str,
    impact: str = "",
) -> tuple[Proposal, bool]:
    """File a proposal, or return the existing claim.

    Returns ``(proposal, created)``. ``created=False`` means an identical claim
    was already pending; the new submitter is merged in and the count does not
    grow. A resubmission with the *same* evidence after a rejection is refused —
    the whole point of a rejection is that this evidence was already judged.
    """
    validate_evidence(evidence)
    digest = evidence_digest(evidence)

    existing = box.find_claim(
        scope_id=scope_id, external_key=external_key, evidence_digest=digest
    )
    if existing is not None:
        merged = box.merge_submitters(
            proposal_id=existing.proposal_id or 0,
            submitters=list(existing.submitters) + [source],
        )
        return merged, False

    rejected = box.find_by_key(scope_id=scope_id, external_key=external_key, state="rejected")
    if rejected is not None and rejected.evidence_digest == digest:
        raise ProposalStaleEvidence(
            f"proposal for {external_key!r} was rejected with this exact evidence; "
            "resubmit only with new evidence",
            external_key=external_key,
            evidence_digest=digest,
        )

    proposal = Proposal(
        scope_id=scope_id,
        source=source,
        kind=kind,
        external_key=external_key,
        body=body,
        evidence=dict(evidence),
        evidence_digest=digest,
        submitted_by=submitted_by,
        impact=impact,
    )
    return box.insert(proposal), True


def _require_pending(box: ProposalBox, *, scope_id: str, proposal_id: int) -> Proposal:
    proposal = box.get(scope_id=scope_id, proposal_id=proposal_id)
    if proposal is None:
        raise InvariantViolated(
            f"proposal {proposal_id} not found in scope {scope_id!r}",
            scope_id=scope_id,
            proposal_id=proposal_id,
        )
    if proposal.state != "pending":
        raise InvariantViolated(
            f"proposal {proposal_id} is already {proposal.state!r}",
            scope_id=scope_id,
            proposal_id=proposal_id,
            state=proposal.state,
        )
    return proposal


def _write_payload(proposal: Proposal) -> dict[str, Any]:
    """The instance payload a confirmed proposal produces.

    A relationship proposal carries its endpoints in the evidence (that is where
    the extractor/agent recorded them), so the write path receives them as attrs.
    """
    evidence = dict(proposal.evidence or {})
    attrs: dict[str, Any] = {}
    for name in ("from_key", "to_key"):
        if evidence.get(name):
            attrs[name] = evidence[name]
    return {"attrs": attrs or None}


def confirm(kernel: Any, box: ProposalBox, *, scope_id: str, proposal_id: int,
            decided_by: str, note: str = "") -> dict[str, Any]:
    """Turn one pending proposal into an instance through the kernel write path."""
    proposal = _require_pending(box, scope_id=scope_id, proposal_id=proposal_id)
    payload = _write_payload(proposal)
    kernel.write(
        scope_id=scope_id,
        kind=proposal.kind,
        external_key=proposal.external_key,
        body=proposal.body,
        actor=decided_by,
        via="proposal",
        attrs=payload["attrs"],
        vocab_name="relationship_kinds" if payload["attrs"] else "entity_types",
        source_ref=proposal.source,
        proposal_id=proposal.proposal_id,
    )
    decided = box.decide(
        proposal_id=proposal.proposal_id or 0,
        state="confirmed",
        decided_by=decided_by,
        note=note,
    )
    return {"proposal": decided, "result": "confirmed"}


def reject(box: ProposalBox, *, scope_id: str, proposal_id: int,
           decided_by: str, note: str = "") -> dict[str, Any]:
    """Refuse one pending proposal (history is kept; identity is not)."""
    proposal = _require_pending(box, scope_id=scope_id, proposal_id=proposal_id)
    decided = box.decide(
        proposal_id=proposal.proposal_id or 0,
        state="rejected",
        decided_by=decided_by,
        note=note,
    )
    return {"proposal": decided, "result": "rejected"}


def confirm_batch(
    kernel: Any,
    box: ProposalBox,
    *,
    scope_id: str,
    proposal_ids: Sequence[int],
    decided_by: str,
) -> dict[str, Any]:
    """Confirm many proposals, one by one.

    A failure is reported for that item and does **not** roll back the ones that
    succeeded: the confirmed instances are already written and journalled, and
    silently undoing them would be a second, undocumented decision.
    """
    results: list[dict[str, Any]] = []
    for proposal_id in proposal_ids:
        try:
            results.append(
                {"proposal_id": proposal_id, **confirm(
                    kernel, box, scope_id=scope_id,
                    proposal_id=proposal_id, decided_by=decided_by,
                )}
            )
        except Exception as exc:  # noqa: BLE001 - reported per item, never swallowed
            results.append(
                {
                    "proposal_id": proposal_id,
                    "result": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    confirmed = [r for r in results if r["result"] == "confirmed"]
    failed = [r for r in results if r["result"] == "failed"]
    return {
        "results": results,
        "confirmed": len(confirmed),
        "failed": len(failed),
        "ok": not failed,
    }


def pending_count(box: ProposalBox, *, scope_id: str) -> int:
    return len(box.list_pending(scope_id=scope_id, limit=1_000_000))


def find_rejected_with_evidence(
    box: ProposalBox, *, scope_id: str, external_key: str, evidence: Mapping[str, Any]
) -> Optional[Proposal]:
    """The rejected proposal that used this exact evidence, if any (diagnostics)."""
    rejected = box.find_by_key(scope_id=scope_id, external_key=external_key, state="rejected")
    if rejected and rejected.evidence_digest == evidence_digest(evidence):
        return rejected
    return None
