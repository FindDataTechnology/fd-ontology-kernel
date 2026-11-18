"""The assertions, each paired with a counterexample that must make it fail.

Two rules govern this file:

* every assertion runs against a **fresh sandbox** from ``AdapterSpec.sandbox()``,
  so one failing check cannot contaminate the next;
* every assertion exposes the checker it uses, and its counterexample calls the
  same checker with a deliberately broken collaborator. If the checker cannot
  tell right from wrong, the counterexample says so before the gate is trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from .. import distribution as distribution_mod
from .. import events as events_mod
from .. import keys
from .. import ledger as ledger_mod
from .. import manifest as manifest_mod
from ..contracts import Consumer, Proposal, Unit
from ..errors import (
    EventAppendOnly,
    InvariantViolated,
    ProposalStaleEvidence,
    ShapeUnknown,
    VocabOutOfSet,
)
from ..keys import FoldRule
from ..kernel import AdapterDeclaration, Kernel
from ..memory import (
    MemoryJournal,
    MemoryProposalBox,
    MemoryPublicationStore,
    MemoryRegistry,
    MemoryUnitStore,
    memory_vocabulary,
)
from .spec import AdapterSpec


@dataclass(frozen=True)
class Assertion:
    id: str
    invariant: str
    description: str
    fix: str
    run: Callable[[AdapterSpec], list[str]]
    counterexample: Callable[[], bool]
    #: Returns a reason string when the assertion cannot apply to this adapter
    #: (e.g. it declares no clause kinds). A skip is reported as a skip — never
    #: counted as a pass, because "not applicable" and "verified" are different
    #: facts.
    applicable: Optional[Callable[[AdapterSpec], Optional[str]]] = None


# ── broken collaborators ─────────────────────────────────────────────────────


class _SharedScopeStore(MemoryUnitStore):
    """Ignores the scope: the classic isolation bug (two tenants, one row)."""

    def get(self, *, scope_id: str, kind: str, external_key: str):
        return super().get(scope_id="shared", kind=kind, external_key=external_key)

    def upsert(self, unit: Unit) -> Unit:
        return super().upsert(
            Unit(**{**unit.__dict__, "scope_id": "shared"})
        )

    def list_live(self, *, scope_id: str) -> list[Unit]:
        return super().list_live(scope_id="shared")

    def live_key_kinds(self, *, scope_id: str) -> dict[str, str]:
        return super().live_key_kinds(scope_id="shared")


class _PermissiveVocabulary:
    """Accepts any kind — the gate is decorative."""

    release = "permissive"

    def require(self, set_name: str, value: str, *, scope_id: str = "") -> None:
        return None

    def has(self, set_name: str, value: str) -> bool:
        return True


class _SilentJournal(MemoryJournal):
    """Accepts events and keeps none: writes look fine, the trail is empty."""

    def append(self, event):
        return event


class _MutableJournal(MemoryJournal):
    """Offers a rewrite path. An append-only journal must not have one."""

    def update(self, event):  # pragma: no cover - existence is the violation
        raise NotImplementedError


class _CommittingBox(MemoryProposalBox):
    """Writing content on nomination: the exact shortcut the spec forbids."""

    def __init__(self, store: MemoryUnitStore) -> None:
        super().__init__()
        self._store = store

    def insert(self, proposal: Proposal) -> Proposal:
        stored = super().insert(proposal)
        self._store.upsert(
            Unit(
                scope_id=proposal.scope_id,
                kind=proposal.kind,
                external_key=proposal.external_key,
                body=proposal.body,
            )
        )
        return stored


class _AnyTokenRegistry(MemoryRegistry):
    """Authenticates any credential: every pull "succeeds", including failures."""

    def find_by_token_hash(self, *, token_hash: str):
        consumers = self.consumers()
        return consumers[0] if consumers else None


def _broken_sandbox(
    *,
    shared_scope: bool = False,
    permissive_vocab: bool = False,
    silent_journal: bool = False,
    mutable_journal: bool = False,
    committing_box: bool = False,
    any_token: bool = False,
    no_fold: bool = False,
    no_clause_gate: bool = False,
    shape: int = 2,
):
    def build() -> Kernel:
        units = _SharedScopeStore() if shared_scope else MemoryUnitStore()
        journal: MemoryJournal = (
            _SilentJournal() if silent_journal
            else _MutableJournal() if mutable_journal
            else MemoryJournal()
        )
        box = _CommittingBox(units) if committing_box else MemoryProposalBox()
        registry = _AnyTokenRegistry() if any_token else MemoryRegistry()
        vocabulary = (
            _PermissiveVocabulary() if permissive_vocab
            else _StaticVocabularyForTests(memory_vocabulary())
        )
        return Kernel(
            units=units,
            events=journal,
            vocabulary=vocabulary,  # type: ignore[arg-type]
            proposals=box,
            publications=MemoryPublicationStore(),
            distribution=registry,
            relationship_kinds=("belongs_to", "cites_law", "defines"),
            clause_kinds=() if no_clause_gate else ("clause",),
            manifest_shape=shape,
            fold_rules_for=(lambda scope: ()) if no_fold else None,
        )

    return build


class _StaticVocabularyForTests:
    """Minimal VocabularySource; kept local so the suite has no import cycles."""

    def __init__(self, vocabulary) -> None:
        self._vocabulary = vocabulary

    def load(self, *, scope_id: str):
        return self._vocabulary


def _broken_spec(**flags: Any) -> AdapterSpec:
    return AdapterSpec(
        name="broken",
        system="conformance-broken",
        declaration=AdapterDeclaration(
            system="conformance-broken",
            kernel_contract="1.0",
            manifest_shape=flags.get("shape", 2),
        ),
        sandbox=_broken_sandbox(**flags),
    )



# ── kind discovery (assertions must not assume a law-shaped vocabulary) ──────


#: Scope id the probe reads the vocabulary for. Sandboxes must answer
#: ``VocabularySource.load`` for any scope (a sandbox may return the same set).
_PROBE_SCOPE = "__conformance_probe__"
#: A value guaranteed absent from any real vocabulary.
_INVALID_KIND = "__definitely_not_a_vocabulary_value__"


def _entity_kind(kernel) -> str:
    """A kind this adapter accepts, taken from its own vocabulary.

    An assertion that hardcodes ``law`` proves nothing about an adapter whose
    vocabulary is ``country``/``concept`` — it just fails. The suite is meant to
    be adapter-agnostic, so each check discovers the kind it writes.
    """
    vocabulary = kernel.vocabulary_for(_PROBE_SCOPE)
    for name in ("entity_types", "entity-type", "entity"):
        hold = vocabulary.sets.get(name)
        if hold is not None and hold.values:
            return hold.values[0]
    raise AssertionError(
        "the adapter's vocabulary exposes no entity kinds; the suite needs at "
        "least one writeable kind"
    )


def _clause_applicability(spec: AdapterSpec) -> Optional[str]:
    """Clause-parent only means something for an adapter that declares clauses."""
    kernel = spec.sandbox()
    kinds = tuple(getattr(kernel, "clause_kinds", ()) or ())
    if not kinds:
        return "adapter declares no clause kinds (the rule is vacuous here)"
    vocabulary = kernel.vocabulary_for(_PROBE_SCOPE)
    if not any(vocabulary.has("entity_types", kind) for kind in kinds):
        return f"adapter vocabulary declares none of the clause kinds {list(kinds)}"
    return None


def _capability_gap(spec: AdapterSpec, name: str, *wanted: str) -> Optional[str]:
    """Reason to skip when this surface declares it lacks a capability.

    ``capabilities is None`` means "not described, assume everything": an adapter
    that says nothing is checked, never skipped. Only an explicit declaration can
    turn a rule into a skip, and the skip quotes that declaration — so a reader
    can tell "verified" from "vacuous here" without reading the adapter.
    """
    capabilities = spec.capabilities
    if capabilities is None:
        return None
    have = capabilities.get(name)
    if have in wanted:
        return None
    return f"surface declares {name}={have!r}; this rule needs {' or '.join(wanted)}"


def _needs_writable_proposals(spec: AdapterSpec) -> Optional[str]:
    return _capability_gap(spec, "proposals", "writable")


def _needs_distribution(spec: AdapterSpec) -> Optional[str]:
    return _capability_gap(spec, "distribution", "present")


# ── checkers (each assertion's real logic lives here) ────────────────────────


def _check_scope_isolation(get, list_live, kind: str) -> list[str]:
    facts: list[str] = []
    left = get(scope_id="scope-a", kind=kind, external_key="common")
    right = get(scope_id="scope-b", kind=kind, external_key="common")
    if left is None or left.body != "A":
        facts.append("scope a lost its own content")
    if right is None or right.body != "B":
        facts.append("scope b saw the other scope's content (isolation broken)")
    if len(list_live(scope_id="scope-a")) != 1:
        facts.append("scope a lists more than its own single unit")
    return facts


def _run_scope_isolation(spec: AdapterSpec) -> list[str]:
    kernel = spec.sandbox()
    kind = _entity_kind(kernel)
    kernel.write(scope_id="scope-a", kind=kind, external_key="common", body="A", actor="gate")
    kernel.write(scope_id="scope-b", kind=kind, external_key="common", body="B", actor="gate")
    return _check_scope_isolation(kernel.units.get, kernel.units.list_live, kind)


def _check_vocab_boundary(write, units_empty, events_empty) -> list[str]:
    facts: list[str] = []
    try:
        write(kind=_INVALID_KIND, external_key="罚款")
        facts.append("an out-of-vocabulary kind was accepted")
    except VocabOutOfSet as exc:
        if not exc.context.get("allowed"):
            facts.append("the refusal did not list the allowed values")
    except Exception as exc:  # noqa: BLE001
        facts.append(f"refusal raised {type(exc).__name__} instead of VocabOutOfSet")
    if not units_empty:
        facts.append("the refused write still left an instance")
    if not events_empty:
        facts.append("the refused write still left an event")
    return facts


def _run_vocab_boundary(spec: AdapterSpec) -> list[str]:
    kernel = spec.sandbox()
    return _check_vocab_boundary(
        lambda *, kind, external_key: kernel.write(
            scope_id="scope-vocab", kind=kind, external_key=external_key,
            body="x", actor="gate",
        ),
        kernel.units.list_live(scope_id="scope-vocab") == [],
        kernel.events.fold(scope_id="scope-vocab") == [],
    )


def _check_folding(write, get, kind: str) -> list[str]:
    facts: list[str] = []
    written = write(external_key="中华人民共和国合同法")
    stored = get(scope_id="scope-fold", kind=kind, external_key="合同法")
    if stored is None:
        facts.append("the folded key was not stored under its canonical form")
    if written.folded_from != "中华人民共和国合同法":
        facts.append("the write did not report which spelling it folded from")
    if stored is not None and "中华人民共和国合同法" not in (stored.attrs.get("aliases") or []):
        facts.append("the original spelling was not kept in aliases")
    return facts


def _run_folding(spec: AdapterSpec) -> list[str]:
    keys.register_rules("scope-fold", (FoldRule("drop-prc", "prefix", "中华人民共和国"),))
    kernel = spec.sandbox()
    kind = _entity_kind(kernel)
    return _check_folding(
        lambda *, external_key: kernel.write(
            scope_id="scope-fold", kind=kind, external_key=external_key,
            body="合同法", actor="gate",
        ),
        kernel.units.get,
        kind,
    )


def _check_clause_parent(write) -> list[str]:
    facts: list[str] = []
    try:
        write(external_key="第十二条")
        facts.append("a bare article number was accepted as a clause key")
    except InvariantViolated:
        pass
    except Exception as exc:  # noqa: BLE001
        facts.append(f"bare clause refused with {type(exc).__name__}, expected InvariantViolated")
    try:
        write(external_key="合同法#第12条")
    except Exception as exc:  # noqa: BLE001
        facts.append(f"a properly parented clause was refused: {type(exc).__name__}")
    return facts


def _run_clause_parent(spec: AdapterSpec) -> list[str]:
    kernel = spec.sandbox()
    return _check_clause_parent(
        lambda *, external_key: kernel.write(
            scope_id="scope-clause", kind="clause", external_key=external_key,
            body="第十二条", actor="gate",
        )
    )


def _check_event_pair(writes: int, journal_events: Sequence, expected_key: str) -> list[str]:
    facts: list[str] = []
    if len(journal_events) != writes:
        facts.append(f"{writes} writes produced {len(journal_events)} events")
    keys_seen = [event.target_key for event in journal_events]
    if expected_key not in keys_seen:
        facts.append(f"no event addressed the written key {expected_key!r}")
    ops = [event.op for event in journal_events]
    if ops[:1] != ["create"]:
        facts.append(f"first event op is {ops[:1]!r}, expected ['create']")
    if writes >= 2 and ops[-1:] != ["update"]:
        facts.append(f"second write produced op {ops[-1:]!r}, expected ['update']")
    return facts


def _run_event_pair(spec: AdapterSpec) -> list[str]:
    kernel = spec.sandbox()
    kernel.write(scope_id="scope-pair", kind=_entity_kind(kernel), external_key="合同法", body="v1", actor="gate")
    kernel.write(scope_id="scope-pair", kind=_entity_kind(kernel), external_key="合同法", body="v2", actor="gate")
    return _check_event_pair(2, kernel.events.fold(scope_id="scope-pair"), "合同法")


def _check_append_only(journal, mutate) -> list[str]:
    facts: list[str] = []
    for forbidden in ("update", "delete", "rewrite", "remove"):
        if hasattr(journal, forbidden):
            facts.append(f"journal exposes {forbidden!r}; an append-only journal has no rewrite path")
    try:
        mutate()
        facts.append("mutating an appended event did not raise")
    except EventAppendOnly:
        pass
    except Exception as exc:  # noqa: BLE001
        facts.append(f"mutation raised {type(exc).__name__}, expected EventAppendOnly")
    return facts


def _run_append_only(spec: AdapterSpec) -> list[str]:
    kernel = spec.sandbox()
    written = kernel.write(scope_id="scope-append", kind=_entity_kind(kernel), external_key="合同法",
                           body="b", actor="gate")
    return _check_append_only(
        kernel.events, lambda: events_mod.attempt_mutation(written.event, after={"body": "x"})
    )


def _check_nomination(write_side_effect_probe, proposed, pending) -> list[str]:
    facts: list[str] = []
    if not proposed[1]:
        facts.append("the first nomination was not reported as created")
    if write_side_effect_probe():
        facts.append("nominating a proposal changed the ontology content")
    if pending != 1:
        facts.append(f"proposal box holds {pending} pending rows after one nomination, expected 1")
    return facts


def _run_nomination(spec: AdapterSpec) -> list[str]:
    kernel = spec.sandbox()
    evidence = {"source": "chunk-1", "quote": "合同应当采用书面形式"}
    proposed = kernel.propose(
        scope_id="scope-propose", source="gate:probe", kind=_entity_kind(kernel),
        external_key="合同法", body="合同法", evidence=evidence, submitted_by="gate",
    )
    return _check_nomination(
        lambda: bool(kernel.units.list_live(scope_id="scope-propose")),
        proposed,
        len(kernel.proposals.list_pending(scope_id="scope-propose")),
    )


def _check_evidence_gate(propose) -> list[str]:
    facts: list[str] = []
    for bad in ({"quote": "只有引文"}, {"source": "chunk-1"}, {"source": "chunk-1", "quote": "  "}):
        try:
            propose(evidence=bad)
            facts.append(f"evidence {bad!r} was accepted; every candidate needs a source and a quote")
        except InvariantViolated:
            pass
        except Exception as exc:  # noqa: BLE001
            facts.append(f"evidence {bad!r} refused with {type(exc).__name__}, expected InvariantViolated")
    return facts


def _run_evidence_gate(spec: AdapterSpec) -> list[str]:
    kernel = spec.sandbox()
    return _check_evidence_gate(
        lambda *, evidence: kernel.propose(
            scope_id="scope-evidence", source="gate:probe", kind=_entity_kind(kernel),
            external_key="合同法", body="合同法", evidence=evidence, submitted_by="gate",
        )
    )


def _check_claim_identity(submit, pending_count) -> list[str]:
    facts: list[str] = []
    evidence = {"source": "chunk-1", "quote": "同一句原文"}
    first = submit(source="post-a", evidence=evidence)
    second = submit(source="post-b", evidence=evidence)
    if not first[1]:
        facts.append("the first claim was not created")
    if second[1]:
        facts.append("an identical claim was inserted a second time")
    if second[0].proposal_id != first[0].proposal_id:
        facts.append("the two submissions got different proposal identities")
    if len(second[0].submitters) != 2:
        facts.append(f"submitters recorded as {second[0].submitters!r}, expected two distinct parties")
    if pending_count() != 1:
        facts.append(f"the box holds {pending_count()} rows for one claim")
    return facts


def _run_claim_identity(spec: AdapterSpec) -> list[str]:
    kernel = spec.sandbox()
    evidence = {"source": "chunk-1", "quote": "同一句原文"}
    return _check_claim_identity(
        lambda *, source, evidence: kernel.propose(
            scope_id="scope-claim", source=source, kind=_entity_kind(kernel), external_key="合同法",
            body="合同法", evidence=evidence, submitted_by=source,
        ),
        lambda: len(kernel.proposals.list_pending(scope_id="scope-claim")),
    )


def _check_stale_evidence(propose, reject, proposal_id_of) -> list[str]:
    facts: list[str] = []
    evidence = {"source": "chunk-1", "quote": "第一次的依据"}
    proposal = propose(evidence)
    reject(proposal_id_of(proposal))
    try:
        propose(evidence)
        facts.append("a rejected claim was accepted again with the same evidence")
    except ProposalStaleEvidence:
        pass
    except Exception as exc:  # noqa: BLE001
        facts.append(f"resubmission raised {type(exc).__name__}, expected ProposalStaleEvidence")
    try:
        fresh = propose({"source": "chunk-2", "quote": "新的依据"})
        if not fresh[1]:
            facts.append("resubmission with new evidence was not created")
    except Exception as exc:  # noqa: BLE001
        facts.append(f"resubmission with new evidence failed: {type(exc).__name__}")
    return facts


def _run_stale_evidence(spec: AdapterSpec) -> list[str]:
    kernel = spec.sandbox()

    def propose(evidence):
        return kernel.propose(
            scope_id="scope-stale", source="gate:probe", kind=_entity_kind(kernel),
            external_key="合同法", body="合同法", evidence=evidence, submitted_by="gate",
        )

    def reject(proposal_id):
        proposal = kernel.proposals.get(scope_id="scope-stale", proposal_id=proposal_id)
        kernel.reject(scope_id="scope-stale", proposal_id=proposal.proposal_id, decided_by="gate")

    return _check_stale_evidence(
        propose, reject, lambda pair: pair[0].proposal_id
    )


def _check_digest_content_only(digest_for, body_changer) -> list[str]:
    facts: list[str] = []
    payload = {"entities": [{"kind": "law", "external_key": "合同法", "body": "合同法",
                             "tier": "raw", "sensitivity": None, "attrs": {}}],
               "relationships": []}
    first = digest_for(payload)
    second = digest_for({**payload})
    if first != second:
        facts.append("the same content produced two different digests")
    changed = body_changer(payload)
    if digest_for(changed) == first:
        facts.append("changing the content did not change the digest")
    return facts


def _run_digest_content_only(spec: AdapterSpec) -> list[str]:
    shape = spec.declaration.manifest_shape

    def digest_for(payload: Mapping[str, Any]) -> str:
        return manifest_mod.digest_for(shape=shape, payload=payload)

    def body_changer(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        changed = {**payload, "entities": [dict(e) for e in payload["entities"]]}
        if shape == manifest_mod.SHAPE_KEY_ADDRESSED:
            changed["entities"][0]["body"] = "中华人民共和国合同法"
            changed["domain"] = payload.get("domain", "")
            changed["template_name"] = payload.get("template_name", "")
            changed["template_version"] = payload.get("template_version", 1)
            changed["entity_types"] = payload.get("entity_types", ["law"])
            changed["relationship_kinds"] = payload.get("relationship_kinds", [])
        else:
            changed["entities"][0]["description"] = "changed"
        return changed

    return _check_digest_content_only(digest_for, body_changer)


def _check_publish_idempotent(publish, count) -> list[str]:
    facts: list[str] = []
    first, unchanged_first = publish()
    second, unchanged_second = publish()
    if unchanged_first:
        facts.append("the first publication was reported as an unchanged re-publication")
    if not unchanged_second:
        facts.append("publishing identical content minted a second version")
    if first.version != second.version or first.digest != second.digest:
        facts.append("identical content produced two different (version, digest) pairs")
    if count() != 1:
        facts.append(f"{count()} versions exist after two identical publications")
    return facts


def _run_publish_idempotent(spec: AdapterSpec) -> list[str]:
    kernel = spec.sandbox()
    kernel.write(scope_id="scope-publish", kind=_entity_kind(kernel), external_key="合同法",
                 body="合同法", actor="gate")
    return _check_publish_idempotent(
        lambda: kernel.publish(scope_id="scope-publish", published_by="gate"),
        kernel.publications.count,
    )


def _check_shape_gate(canonical, declared_shape: int, supported: Sequence[int]) -> list[str]:
    facts: list[str] = []
    if declared_shape not in supported:
        facts.append(f"declared shape {declared_shape} is not in {list(supported)}")
    try:
        canonical(9999)
        facts.append("an unknown manifest shape was accepted instead of refused")
    except ShapeUnknown:
        pass
    except Exception as exc:  # noqa: BLE001
        facts.append(f"unknown shape raised {type(exc).__name__}, expected ShapeUnknown")
    return facts


def _run_shape_gate(spec: AdapterSpec) -> list[str]:
    shape = spec.declaration.manifest_shape
    return _check_shape_gate(
        lambda version: manifest_mod.canonical_payload(shape=version, payload={"entities": []}),
        shape,
        sorted(manifest_mod.SUPPORTED_SHAPES),
    )


def _check_pull_trail(pull, pulls, consumers_after) -> list[str]:
    facts: list[str] = []
    published = pull(version=None, token="good")
    if published is None:
        facts.append("a valid consumer could not pull a published version")
    failed = 0
    for token, version in (("bad", None), ("good", 9999)):
        try:
            pull(version=version, token=token)
        except InvariantViolated:
            failed += 1
        except Exception as exc:  # noqa: BLE001
            facts.append(f"failed pull raised {type(exc).__name__}, expected InvariantViolated")
    if failed != 2:
        facts.append("a failing pull did not raise; refusals must be loud")
    if len(pulls()) != 1:
        facts.append(f"the trail holds {len(pulls())} pulls after 1 success and 2 failures")
    if consumers_after and consumers_after[0].revoked:
        facts.append("pulling modified the consumer record itself")
    return facts


def _run_pull_trail(spec: AdapterSpec) -> list[str]:
    kernel = spec.sandbox()
    registry = kernel.distribution
    registry.register(name="reader", scopes=("scope-pull",),
                      token_hash=distribution_mod.token_hash("good"), created_by="gate")
    kernel.write(scope_id="scope-pull", kind=_entity_kind(kernel), external_key="合同法", body="b", actor="gate")
    kernel.publish(scope_id="scope-pull", published_by="gate")

    def pull(*, version, token):
        publication, _consumer, _event = distribution_mod.pull(
            registry=registry, store=kernel.publications,
            token=token, scope_id="scope-pull", version=version,
        )
        return publication

    return _check_pull_trail(pull, registry.pulls_since, registry.consumers())


def _check_ledger_readonly(build_report, snapshot) -> list[str]:
    facts: list[str] = []
    before = snapshot()
    report = build_report()
    after = snapshot()
    if before != after:
        facts.append(f"opening the ledger changed state: {before} -> {after}")
    if not report.get("curation", {}).get("total"):
        facts.append("the ledger projection reported no curated content")
    if "reconcile" not in report:
        facts.append("the ledger did not include reconciliation facts")
    return facts


def _run_ledger_readonly(spec: AdapterSpec) -> list[str]:
    kernel = spec.sandbox()
    kernel.write(scope_id="scope-ledger", kind=_entity_kind(kernel), external_key="合同法", body="b", actor="gate")

    def build_report() -> dict:
        return ledger_mod.project(
            vocabulary=kernel.vocabulary_for("scope-ledger"),
            units=kernel.live_units(scope_id="scope-ledger"),
        ).as_dict()

    def snapshot() -> tuple:
        return (
            kernel.units.raw_count(),
            len(kernel.events.fold(scope_id="scope-ledger")),
            kernel.publications.count(),
        )

    return _check_ledger_readonly(build_report, snapshot)


def _check_reconcile_facts(build_report) -> list[str]:
    report = build_report()
    facts: list[str] = []
    reconcile = report.get("reconcile") or {}
    for field in ("unused_entity_types", "out_of_vocabulary", "unpublished_changes",
                  "never_pulled_consumers"):
        if field not in reconcile:
            facts.append(f"reconcile omits {field!r}")
    if report.get("smoke") is None:
        facts.append("the ledger does not report smoke leftovers separately from gaps")
    if "gaps" not in report:
        facts.append("the ledger does not report declared-but-unused types")
    return facts


def _run_reconcile_facts(spec: AdapterSpec) -> list[str]:
    kernel = spec.sandbox()
    kernel.write(scope_id="scope-reconcile", kind=_entity_kind(kernel), external_key="合同法",
                 body="b", actor="gate")
    kernel.write(scope_id="scope-reconcile", kind=_entity_kind(kernel), external_key="smoke-x",
                 body="冒烟", actor="gate")

    def build_report() -> dict:
        return ledger_mod.project(
            vocabulary=kernel.vocabulary_for("scope-reconcile"),
            units=kernel.live_units(scope_id="scope-reconcile"),
            unpublished_changes=2,
        ).as_dict()

    return _check_reconcile_facts(build_report)


# ── the registry ─────────────────────────────────────────────────────────────

ASSERTIONS: list[Assertion] = [
    Assertion(
        id="scope-isolation",
        invariant="实例身份由作用域、类型与外部键共同确定",
        description="两个作用域写同一外部键时互不覆盖；列表只返回本作用域内容",
        fix="UnitStore 的每个查询都必须带 scope_id 过滤，且主键含 scope_id",
        run=_run_scope_isolation,
        counterexample=lambda: bool(
            _check_scope_isolation(
                lambda **kw: Unit(scope_id="shared", kind=kw["kind"],
                                  external_key=kw["external_key"],
                                  body="A" if kw["scope_id"] == "scope-a" else "B"),
                lambda **kw: [Unit(scope_id="shared", kind="law", external_key="common", body="A"),
                              Unit(scope_id="shared", kind="law", external_key="common", body="B")],
                "law",
            )
        ),
    ),
    Assertion(
        id="vocab-boundary",
        invariant="写入类型必须在作用域锚定的封闭词表内",
        description="词表外类型被拒，错误列出可用值，且不留实例与事件",
        fix="VocabularySource.load() 必须返回该作用域真实的封闭集合；不要用宽松实现兜底",
        run=_run_vocab_boundary,
        counterexample=lambda: bool(_run_vocab_boundary(_broken_spec(permissive_vocab=True))),
    ),
    Assertion(
        id="write-boundary-folding",
        invariant="词的归一名只在唯一写入口发生",
        description="注册规则后，写入写法被归名、原写法进 aliases、写入结果报告 folded_from",
        fix="Kernel 必须用该作用域注册的规则折叠；适配层不得自己再折叠一次",
        run=_run_folding,
        counterexample=lambda: bool(_run_folding(_broken_spec(no_fold=True))),
    ),
    Assertion(
        id="clause-parent",
        invariant="条款类键必须携带父键",
        description="裸条文号被拒，带父键的条款键被接受",
        fix="clause_kinds 必须包含该系统的条款类型；不得为兼容旧数据放开父键检查",
        run=_run_clause_parent,
        counterexample=lambda: bool(_run_clause_parent(_broken_spec(no_clause_gate=True))),
        applicable=_clause_applicability,
    ),
    Assertion(
        id="write-event-pair",
        invariant="写入与事件在同一事务内成对落地",
        description="N 次写入产生 N 条事件，op 依次为 create/update，事件按语义键寻址",
        fix="EventJournal.append 必须真正落库；静默丢弃事件的实现不算实现",
        run=_run_event_pair,
        counterexample=lambda: bool(_run_event_pair(_broken_spec(silent_journal=True))),
    ),
    Assertion(
        id="event-append-only",
        invariant="事件只追加，不存在改写路径",
        description="日志对象没有 update/delete/remove/rewrite；改写事件被拒",
        fix="删掉日志上的任何改写方法；需要更正时追加上一条更正事件",
        run=_run_append_only,
        counterexample=lambda: bool(_run_append_only(_broken_spec(mutable_journal=True))),
    ),
    Assertion(
        id="nomination-not-ontology",
        invariant="机器只能提名，人确认才进本体",
        description="一次提名只增加待确认提案，本体实例与事件数量不变",
        fix="ProposalBox.insert 不得写实例；确认必须走 Kernel.confirm → Kernel.write",
        run=_run_nomination,
        counterexample=lambda: bool(_run_nomination(_broken_spec(committing_box=True))),
        applicable=_needs_writable_proposals,
    ),
    Assertion(
        id="evidence-required",
        invariant="候选必须携带可校验的证据",
        description="缺来源或缺引文的候选被拒",
        fix="提案入口必须先校验 evidence（source + quote），不得先入库再补证据",
        run=_run_evidence_gate,
        counterexample=lambda: bool(
            _check_evidence_gate(lambda *, evidence: None)
        ),
    ),
    Assertion(
        id="claim-identity",
        invariant="提案身份由主张与证据共同决定",
        description="两方同键同证据只留一行，两个提交方都被记录",
        fix="find_claim 必须按 (scope, 外部键, 证据摘要) 查 pending，不能按提交方去重",
        run=_run_claim_identity,
        applicable=_needs_writable_proposals,
        counterexample=lambda: bool(
            _check_claim_identity(
                lambda *, source, evidence: (Proposal(
                    scope_id="s", source=source, kind="law", external_key="k", body="b",
                    evidence=evidence, evidence_digest=str(id(evidence)), submitted_by=source,
                ), True),
                lambda: 2,
            )
        ),
    ),
    Assertion(
        id="stale-evidence",
        invariant="提案身份由主张与证据共同决定",
        description="被驳回的主张以同证据重提被拒，换证据可重提",
        fix="决定为 rejected 后，同证据重提必须抛 PROPOSAL_STALE_EVIDENCE",
        run=_run_stale_evidence,
        applicable=_needs_writable_proposals,
        counterexample=lambda: bool(
            _check_stale_evidence(
                propose=lambda evidence: (Proposal(
                    scope_id="s", source="p", kind="law", external_key="k", body="b",
                    evidence=evidence, evidence_digest="same", submitted_by="p",
                ), True),
                reject=lambda pid: None,
                proposal_id_of=lambda pair: 1,
            )
        ),
    ),
    Assertion(
        id="digest-content-only",
        invariant="快照只锚定内容",
        description="同内容同 digest，改内容即变 digest，与该系统的形状版本一致",
        fix="digest 只能覆盖规范 manifest；时间、发布者、版本号不得进入 payload",
        run=_run_digest_content_only,
        counterexample=lambda: bool(
            _check_digest_content_only(lambda payload: "constant", lambda payload: payload)
        ),
    ),
    Assertion(
        id="publish-idempotent",
        invariant="快照只锚定内容，版本单调且幂等",
        description="相同内容重复发布不新增版本，返回 unchanged",
        fix="发布前按 digest 查既有版本；命中即返回，不铸造一个内容相同的新版本",
        run=_run_publish_idempotent,
        counterexample=lambda: bool(
            _check_publish_idempotent(
                lambda: (type("P", (), {"version": 1, "digest": "d"})(), False), lambda: 2
            )
        ),
    ),
    Assertion(
        id="shape-version",
        invariant="manifest 形状必须显式声明版本",
        description="声明的形状受支持；未知形状被拒而不是猜",
        fix="适配方的 declaration.manifest_shape 必须是内核支持的版本；不得自行扩展 payload 形状",
        run=_run_shape_gate,
        counterexample=lambda: bool(
            _check_shape_gate(lambda version: {"entities": []}, 99, [1, 2])
        ),
    ),
    Assertion(
        id="pull-trail",
        invariant="分发账本只记真实发生的成功拉取",
        description="成功拉取留一条记录；错误凭据与不存在版本都不留痕",
        fix="record_pull 只在验明凭据并解析到发布版本之后调用；失败分支必须直接抛出",
        run=_run_pull_trail,
        counterexample=lambda: bool(_run_pull_trail(_broken_spec(any_token=True))),
        applicable=_needs_distribution,
    ),
    Assertion(
        id="ledger-readonly",
        invariant="账本由四层只读投影组成且不写任何东西",
        description="打开账本前后实例、事件、发布数量不变，投影仍包含内容与核对",
        fix="账本必须是纯投影；不得引入缓存表或在打开时回写",
        run=_run_ledger_readonly,
        counterexample=lambda: bool(
            _check_ledger_readonly(
                lambda: _WRITES.append(1) or {"curation": {"total": 1}, "reconcile": {}},
                lambda: len(_WRITES),
            )
        ),
    ),
    Assertion(
        id="reconcile-facts",
        invariant="一致性核对只报事实不自动修复",
        description="核对结果含未用词表、越界实例、未发布变更、未拉取消费者；冒烟数据单独报",
        fix="reconcile 输出必须包含四类事实，且不得触发任何写入或自动修复",
        run=_run_reconcile_facts,
        counterexample=lambda: bool(
            _check_reconcile_facts(lambda: {"curation": {"total": 1}, "reconcile": {}})
        ),
    ),
]

_WRITES: list[int] = []

BY_ID: dict[str, Assertion] = {assertion.id: assertion for assertion in ASSERTIONS}
