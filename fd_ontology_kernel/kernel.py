"""The kernel surface: one write path, one publication path, one adapter check.

``Kernel.write`` is the only function that changes ontology content. It does, in
order, exactly four things:

1. ask the scope's vocabulary whether ``kind`` is allowed (refuse otherwise);
2. fold the external key with the scope's registered rules (the *write
   boundary*, so no caller can forget to);
3. upsert the instance through the unit store;
4. append one event through the journal, derived from what was there before.

Steps 3 and 4 happen inside the caller's transaction. If the append fails the
upsert must roll back with it — an instance that changed without a trail entry
is precisely the failure this design exists to prevent.

Everything else here is a thin composition: publication delegates to
``manifest``, nomination to ``proposals``, distribution to ``distribution``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from . import events as events_mod
from . import manifest as manifest_mod
from . import proposals as proposals_mod
from .contracts import Event, Proposal, Publication, Relationship, Unit
from .errors import InvariantViolated, PortMissing, ShapeDeclarationMissing
from .keys import clause_no, fold as fold_key
from .manifest import SUPPORTED_SHAPES
from .ports import (
    DistributionRegistry,
    EventJournal,
    ProposalBox,
    PublicationStore,
    REQUIRED_PORTS,
    UnitStore,
    VocabularySource,
)
from .vocab import Vocabulary

#: Version of the *kernel contract* (ports + error codes + shapes). Adapters
#: declare which one they implement; a major mismatch is refused.
KERNEL_CONTRACT_VERSION = "1.0"


@dataclass(frozen=True)
class Written:
    """What one write produced: the stored unit, its event, and any folding."""

    unit: Unit
    event: Event
    folded_from: Optional[str] = None
    created: bool = True


@dataclass(frozen=True)
class AdapterDeclaration:
    """What an adapter must state before the kernel will serve it."""

    system: str
    kernel_contract: str
    manifest_shape: int


def validate_declaration(
    declaration: Optional[AdapterDeclaration],
    *,
    kernel_contract: str = KERNEL_CONTRACT_VERSION,
) -> AdapterDeclaration:
    """Refuse to run for an adapter that has not declared itself, or declared wrong."""
    if declaration is None:
        raise ShapeDeclarationMissing(
            "adapter did not declare its kernel contract version and manifest shape; "
            f"pass AdapterDeclaration(kernel_contract={kernel_contract!r}, manifest_shape=<1|2>)"
        )
    if not declaration.kernel_contract:
        raise ShapeDeclarationMissing("adapter declared an empty kernel contract version")
    if declaration.kernel_contract.split(".")[0] != kernel_contract.split(".")[0]:
        raise ShapeDeclarationMissing(
            f"adapter implements kernel contract {declaration.kernel_contract!r}; "
            f"this kernel speaks {kernel_contract!r} (major mismatch)",
            adapter=declaration.kernel_contract,
            kernel=kernel_contract,
        )
    if declaration.manifest_shape not in SUPPORTED_SHAPES:
        raise ShapeDeclarationMissing(
            f"adapter declared manifest shape {declaration.manifest_shape!r}; "
            f"supported: {sorted(SUPPORTED_SHAPES)}",
            declared=declaration.manifest_shape,
            supported=sorted(SUPPORTED_SHAPES),
        )
    return declaration


def _unit_snapshot(unit: Optional[Unit]) -> Optional[dict[str, Any]]:
    """The before/after image stored in an event.

    An adapter that has its own row image supplies it on ``Unit.raw``; that shape
    is often a published contract (surrogate id, timestamps) and must not be
    silently replaced by the kernel's portable projection. Without one, the
    portable image is used — which is what the in-memory adapter and the tests
    exercise.
    """
    if unit is None:
        return None
    if unit.raw is not None:
        return dict(unit.raw)
    return {
        "kind": unit.kind,
        "external_key": unit.external_key,
        "body": unit.body,
        "tier": unit.tier,
        "tags": dict(unit.tags),
        "attrs": dict(unit.attrs),
        "source_path": unit.source_path,
        "provenance": dict(unit.provenance),
    }


class Kernel:
    """The mechanism bundle, wired to one adapter's six ports."""

    def __init__(
        self,
        *,
        units: UnitStore,
        events: EventJournal,
        vocabulary: VocabularySource,
        proposals: Optional[ProposalBox] = None,
        publications: Optional[PublicationStore] = None,
        distribution: Optional[DistributionRegistry] = None,
        relationship_kinds: Sequence[str] = (),
        clause_kinds: Sequence[str] = ("clause",),
        manifest_shape: int = 1,
        declaration: Optional[AdapterDeclaration] = None,
        fold_rules_for=None,
    ) -> None:
        supplied = {
            "units": units,
            "events": events,
            "vocabulary": vocabulary,
        }
        for name in REQUIRED_PORTS:
            if supplied.get(name) is None:
                raise PortMissing(
                    f"kernel requires the {name!r} port; it owns no storage of its own",
                    port=name,
                )
        for name, value in (
            ("proposals", proposals),
            ("publications", publications),
            ("distribution", distribution),
        ):
            if value is None:
                continue
            # runtime_checkable protocols check method presence only; that is
            # enough to catch "passed the wrong object" without pretending to
            # type-check an adapter.
        self.units = units
        self.events = events
        self.vocabulary = vocabulary
        self.proposals = proposals
        self.publications = publications
        self.distribution = distribution
        self.relationship_kinds = tuple(relationship_kinds)
        self.clause_kinds = tuple(clause_kinds)
        self.manifest_shape = manifest_shape
        self.declaration = validate_declaration(declaration) if declaration else None
        #: Called with a scope id to obtain its fold rules. Defaults to the
        #: process-wide registry the domain packs populate.
        self._fold_rules_for = fold_rules_for

    # ── vocabulary & keys ────────────────────────────────────────────────────

    def vocabulary_for(self, scope_id: str) -> Vocabulary:
        return self.vocabulary.load(scope_id=scope_id)

    def rules_for(self, scope_id: str) -> tuple:
        if self._fold_rules_for is not None:
            return tuple(self._fold_rules_for(scope_id))
        from .keys import rules_for as registry_rules

        return registry_rules(scope_id)

    def fold_key(
        self, *, scope_id: str, external_key: str
    ) -> tuple[str, list[str]]:
        """Fold a key at the write boundary; never returns an empty key."""
        raw = (external_key or "").strip()
        if not raw:
            return raw, []
        folded, applied = fold_key(raw, self.rules_for(scope_id))
        folded = (folded or "").strip()
        if not folded:
            return raw, []
        return folded, applied

    def _require_clause_parent(self, *, kind: str, external_key: str) -> None:
        """A clause is never addressable by its article number alone.

        The rule is about *bare article numbers*, not about every key of a clause
        kind: a document number such as ``gb-50011`` is a legitimate external key
        and does not parse as a clause address.
        """
        if kind not in self.clause_kinds:
            return
        if "#" in external_key:
            return
        if clause_no(external_key) is not None:
            raise InvariantViolated(
                f"a clause key must be '<parent>#第N条'; {external_key!r} is a bare "
                "article number and matches nearly every document",
                kind=kind,
                external_key=external_key,
            )

    # ── the write path ───────────────────────────────────────────────────────

    def write(
        self,
        *,
        scope_id: str,
        kind: str,
        external_key: str,
        body: str = "",
        actor: str,
        via: str = "kernel",
        tags: Optional[Mapping[str, Any]] = None,
        attrs: Optional[Mapping[str, Any]] = None,
        tier: str = "raw",
        source_path: Optional[str] = None,
        provenance: Optional[Mapping[str, Any]] = None,
        vocab_name: str = "entity_types",
        target_ref: Optional[Any] = None,
        seq: Optional[int] = None,
        source_ref: Optional[str] = None,
        source_instance_id: Optional[int] = None,
        proposal_id: Optional[int] = None,
    ) -> Written:
        """The one path that changes content. See the module docstring."""
        if not actor:
            raise InvariantViolated("every write needs an actor (who did it)", via=via)

        vocabulary = self.vocabulary_for(scope_id)
        vocabulary.require(vocab_name, kind, scope_id=scope_id)

        raw_key = (external_key or "").strip()
        folded_key, applied = self.fold_key(scope_id=scope_id, external_key=raw_key)
        self._require_clause_parent(kind=kind, external_key=folded_key)

        merged_attrs = dict(attrs or {})
        if applied:
            aliases = set(merged_attrs.get("aliases") or [])
            aliases.add(raw_key)
            merged_attrs["aliases"] = sorted(aliases)
            merged_attrs["folded_by"] = list(applied)

        merged_provenance = dict(provenance or {})
        merged_provenance.setdefault("via", via)
        if proposal_id is not None:
            merged_provenance.setdefault("proposal_id", proposal_id)

        candidate = Unit(
            scope_id=scope_id,
            kind=kind,
            external_key=folded_key,
            body=body,
            tier=tier,
            tags=dict(tags or {}),
            attrs=merged_attrs,
            source_path=source_path,
            provenance=merged_provenance,
        )

        existing = self.units.get(
            scope_id=scope_id, kind=kind, external_key=folded_key
        )
        stored = self.units.upsert(candidate)

        event = events_mod.build_event(
            scope_id=scope_id,
            op="create" if existing is None else "update",
            target_kind=kind,
            target_key=folded_key,
            actor=actor,
            via=via,
            before=_unit_snapshot(existing),
            after=_unit_snapshot(stored),
            target_ref=target_ref,
            seq=seq if seq is not None else self._next_seq(scope_id),
            source_ref=source_ref,
            source_instance_id=source_instance_id,
        )
        self.events.append(event)

        return Written(
            unit=stored,
            event=event,
            folded_from=raw_key if folded_key != raw_key else None,
            created=existing is None,
        )

    def _require_known_kind(self, *, scope_id: str, kind: str) -> None:
        """For a delete: the kind only has to belong to *some* closed set.

        A delete never introduces a kind, so the question is not "is this an
        entity type" but "was this ever an allowed kind here". Gating a
        relationship delete against ``entity_types`` would refuse every
        relationship.
        """
        vocabulary = self.vocabulary_for(scope_id)
        if any(hold.has(kind) for hold in vocabulary.sets.values()):
            return
        allowed = sorted({value for hold in vocabulary.sets.values() for value in hold.values})
        from .errors import VocabOutOfSet

        raise VocabOutOfSet(
            f"kind {kind!r} is not in any vocabulary of scope {scope_id!r}; "
            f"allowed kinds: {allowed}",
            set_name="*",
            value=kind,
            allowed=allowed,
            scope_id=scope_id,
        )

    def remove(
        self,
        *,
        scope_id: str,
        kind: str,
        external_key: str,
        actor: str,
        via: str = "kernel",
        vocab_name: Optional[str] = None,
        source_ref: Optional[str] = None,
        proposal_id: Optional[int] = None,
    ) -> Written:
        """Soft-delete: keep the content, flag it, and journal a ``delete``.

        A delete is not an update with a flag — the trail has to say "removed",
        or a consumer folding the events cannot tell a rename from a removal. The
        row keeps its body and metadata so the history stays replayable.
        """
        if not actor:
            raise InvariantViolated("every write needs an actor (who did it)", via=via)
        if vocab_name is None:
            self._require_known_kind(scope_id=scope_id, kind=kind)
        else:
            self.vocabulary_for(scope_id).require(vocab_name, kind, scope_id=scope_id)
        key, _applied = self.fold_key(scope_id=scope_id, external_key=external_key)
        prior = self.units.get(scope_id=scope_id, kind=kind, external_key=key)
        if prior is None:
            raise InvariantViolated(
                f"unit {kind!r}/{key!r} not found in scope {scope_id!r}",
                scope_id=scope_id, kind=kind, external_key=key,
            )
        tags = dict(prior.tags or {})
        tags["deleted"] = True
        provenance = dict(prior.provenance or {})
        provenance["via"] = via
        if proposal_id is not None:
            provenance["proposal_id"] = proposal_id
        stored = self.units.upsert(
            Unit(
                scope_id=scope_id,
                kind=kind,
                external_key=key,
                body=prior.body,
                tier=prior.tier,
                tags=tags,
                attrs=dict(prior.attrs or {}),
                source_path=prior.source_path,
                provenance=provenance,
            )
        )
        event = events_mod.build_event(
            scope_id=scope_id,
            op="delete",
            target_kind=kind,
            target_key=key,
            actor=actor,
            via=via,
            before=_unit_snapshot(prior),
            after=None,
            source_ref=source_ref,
        )
        self.events.append(event)
        return Written(unit=stored, event=event, created=False)

    def _next_seq(self, scope_id: str) -> Optional[int]:
        """Ask the journal for the next sequence number, when it keeps one.

        A surface that addresses by key has no seq; returning ``None`` is how the
        envelope says so rather than inventing a number the store will not keep.
        """
        next_seq = getattr(self.events, "next_seq", None)
        if next_seq is None:
            return None
        return int(next_seq(scope_id=scope_id))

    def write_relationship(
        self,
        *,
        scope_id: str,
        kind: str,
        from_key: str,
        to_key: str,
        actor: str,
        body: str = "",
        via: str = "kernel",
        tags: Optional[Mapping[str, Any]] = None,
        attrs: Optional[Mapping[str, Any]] = None,
        tier: str = "raw",
        **kwargs: Any,
    ) -> Written:
        """Write a relationship, checking both endpoints are live in this scope.

        Endpoints must already exist (or be created earlier in the same batch by
        the caller) — a relationship to nothing is not a relationship.
        """
        live = self.units.live_key_kinds(scope_id=scope_id)
        missing = [key for key in (from_key, to_key) if key not in live]
        if missing:
            raise InvariantViolated(
                f"relationship endpoint(s) not live in scope {scope_id!r}: {missing}",
                scope_id=scope_id,
                missing=missing,
            )
        relationship = Relationship(
            scope_id=scope_id,
            kind=kind,
            from_key=from_key,
            to_key=to_key,
            body=body,
            tier=tier,
            tags=dict(tags or {}),
            attrs=dict(attrs or {}),
        )
        unit = relationship.to_unit()
        created = self.write(
            scope_id=scope_id,
            kind=kind,
            external_key=unit.external_key,
            body=unit.body,
            actor=actor,
            via=via,
            tags=unit.tags,
            attrs=unit.attrs,
            tier=tier,
            vocab_name="relationship_kinds",
            **kwargs,
        )
        return created

    # ── proposals ────────────────────────────────────────────────────────────

    def _require_proposals(self) -> ProposalBox:
        if self.proposals is None:
            raise PortMissing("this kernel has no proposals port configured", port="proposals")
        return self.proposals

    def propose(
        self,
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
        """File a nomination, folding the key at the same boundary writes use.

        A proposal that keeps an unfolded key produces a unit under a different
        key at confirm time — the duplicate is created by the inbox, not by the
        ontology. So folding happens here too, and the original spelling is kept
        in ``impact`` (the proposal row has no attributes column) rather than
        silently dropped.
        """
        vocabulary = self.vocabulary_for(scope_id)
        vocab_name = "relationship_kinds" if evidence.get("from_key") or evidence.get("to_key") else "entity_types"
        vocabulary.require(vocab_name, kind, scope_id=scope_id)

        raw_key = (external_key or "").strip()
        folded_key, applied = self.fold_key(scope_id=scope_id, external_key=raw_key)
        if applied:
            note = f"键已归名：{raw_key} → {folded_key}（规则 {', '.join(applied)}）"
            impact = f"{note}；{impact}".strip("；") if impact else note

        return proposals_mod.submit(
            self._require_proposals(),
            scope_id=scope_id,
            source=source,
            kind=kind,
            external_key=folded_key or raw_key,
            body=body,
            evidence=evidence,
            submitted_by=submitted_by,
            impact=impact,
        )

    def confirm(self, *, scope_id: str, proposal_id: int, decided_by: str,
                note: str = "") -> dict[str, Any]:
        return proposals_mod.confirm(
            self, self._require_proposals(),
            scope_id=scope_id, proposal_id=proposal_id, decided_by=decided_by, note=note,
        )

    def reject(self, *, scope_id: str, proposal_id: int, decided_by: str,
               note: str = "") -> dict[str, Any]:
        return proposals_mod.reject(
            self._require_proposals(),
            scope_id=scope_id, proposal_id=proposal_id, decided_by=decided_by, note=note,
        )

    # ── publication ──────────────────────────────────────────────────────────

    def _require_publications(self) -> PublicationStore:
        if self.publications is None:
            raise PortMissing(
                "this kernel has no publications port configured", port="publications"
            )
        return self.publications

    def build_payload(
        self,
        *,
        scope_id: str,
        header: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Current state → canonical payload in this kernel's declared shape."""
        live = self.units.list_live(scope_id=scope_id)
        relationship_kinds = set(self.relationship_kinds)
        entities = [
            self._entry_for(unit)
            for unit in live
            if unit.kind not in relationship_kinds
        ]
        relationships = [
            self._entry_for(unit)
            for unit in live
            if unit.kind in relationship_kinds
        ]
        return manifest_mod.build_payload(
            shape=self.manifest_shape,
            entities=entities,
            relationships=relationships,
            header=header,
        )

    def _entry_for(self, unit: Unit) -> dict[str, Any]:
        """Project a unit onto the payload entry shape this kernel declares."""
        if self.manifest_shape == manifest_mod.SHAPE_KEY_ADDRESSED:
            entry: dict[str, Any] = {
                "kind": unit.kind,
                "external_key": unit.external_key,
                "body": unit.body,
                "tier": unit.tier,
                "sensitivity": (unit.tags or {}).get("sensitivity"),
                "attrs": dict(unit.attrs or {}),
            }
            if unit.kind in set(self.relationship_kinds):
                entry["from_key"] = (unit.attrs or {}).get("from_key")
                entry["to_key"] = (unit.attrs or {}).get("to_key")
            return entry
        # Shape 1 is id-addressed; an adapter on that shape must supply the raw
        # rows (it owns the ids), so this projection is only a fallback.
        return {
            "id": None,
            "name": (unit.attrs or {}).get("name") or unit.external_key,
            "dataset_id": (unit.attrs or {}).get("dataset_id"),
            "pk_columns": list((unit.attrs or {}).get("pk_columns") or []),
            "description": unit.body,
            "display_column": (unit.attrs or {}).get("display_column"),
            "column_notes": (unit.attrs or {}).get("column_notes"),
            "tier": unit.tier,
            "sensitivity": (unit.tags or {}).get("sensitivity"),
        }

    def publish(
        self,
        *,
        scope_id: str,
        published_by: str,
        payload: Optional[Mapping[str, Any]] = None,
        header: Optional[Mapping[str, Any]] = None,
        trigger: str = "manual",
        label: Optional[str] = None,
        allow_empty: bool = False,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> tuple[Publication, bool]:
        """Freeze the current content into a version; idempotent by digest.

        Returns ``(publication, unchanged)``. ``unchanged=True`` means the content
        matched an existing version and nothing was inserted — the caller reports
        "identical to vN" rather than minting a version that says nothing new.
        """
        store = self._require_publications()
        raw = payload if payload is not None else self.build_payload(
            scope_id=scope_id, header=header
        )
        canonical = manifest_mod.canonical_payload(shape=self.manifest_shape, payload=raw)
        if not allow_empty and manifest_mod.is_empty(canonical):
            raise InvariantViolated(
                "refusing to publish an empty payload: an empty snapshot says "
                "nothing and cannot be told apart from a failed read",
                scope_id=scope_id,
            )
        digest = manifest_mod.digest_of(canonical)
        existing = store.get(scope_id=scope_id, digest=digest)
        if existing is not None:
            return existing, True
        publication = Publication(
            scope_id=scope_id,
            version=store.next_version(scope_id=scope_id),
            digest=digest,
            payload=canonical,
            manifest_schema_version=self.manifest_shape,
            trigger=trigger,
            label=label,
            created_by=published_by,
            extra=dict(extra or {}),
        )
        return store.insert(publication), False

    # ── reads used by the ledger ─────────────────────────────────────────────

    def live_units(self, *, scope_id: str) -> list[Unit]:
        return self.units.list_live(scope_id=scope_id)

    def publications_for(self, *, scope_id: str) -> list[Publication]:
        """All publications, newest first (`latest` is the common case)."""
        store = self._require_publications()
        latest = store.latest(scope_id=scope_id)
        return [latest] if latest is not None else []

    def iter_relationship_kinds(self) -> Iterable[str]:
        return iter(self.relationship_kinds)
