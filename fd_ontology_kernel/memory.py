"""An in-memory adapter implementing all six ports.

Two uses, both real:

* the kernel's own tests need a store without a database;
* an adapter author needs a worked example of every port, including the parts
  that are easy to get subtly wrong (seq allocation, claim identity, pull
  recording only on success).

It is intentionally plain: dicts and lists, no locking, no persistence. Anything
that needs durability implements the ports against its own storage instead.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Optional, Sequence

from .contracts import Consumer, Event, Proposal, Publication, PullEvent, Unit
from .vocab import Vocabulary


class MemoryUnitStore:
    """Units keyed by (scope, kind, external_key); deletes are a tag, not a row loss."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], Unit] = {}

    def get(self, *, scope_id: str, kind: str, external_key: str) -> Optional[Unit]:
        return self._rows.get((scope_id, kind, external_key))

    def upsert(self, unit: Unit) -> Unit:
        self._rows[(unit.scope_id, unit.kind, unit.external_key)] = unit
        return unit

    def list_live(self, *, scope_id: str) -> list[Unit]:
        return [
            unit for (scope, _kind, _key), unit in self._rows.items()
            if scope == scope_id and not (unit.tags or {}).get("deleted")
        ]

    def live_key_kinds(self, *, scope_id: str) -> dict[str, str]:
        return {
            unit.external_key: unit.kind
            for unit in self.list_live(scope_id=scope_id)
        }

    # test affordance, not part of the port
    def raw_count(self) -> int:
        return len(self._rows)


class MemoryJournal:
    """Append-only list with a per-scope sequence counter."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._seq: dict[str, int] = {}

    def next_seq(self, *, scope_id: str) -> int:
        return self._seq.get(scope_id, 0) + 1

    def append(self, event: Event) -> Event:
        if not isinstance(event, Event):
            raise TypeError("the journal appends Event objects only")
        seq = event.seq
        if seq is None:
            seq = self.next_seq(scope_id=event.scope_id)
            event = replace(event, seq=seq)
        self._seq[event.scope_id] = max(self._seq.get(event.scope_id, 0), int(seq))
        if not event.occurred_at:
            event = replace(event, occurred_at=f"t{seq:06d}")
        self._events.append(event)
        return event

    def fold(self, *, scope_id: str) -> list[Event]:
        return [event for event in self._events if event.scope_id == scope_id]

    def count_since(
        self,
        *,
        scope_id: str,
        seq: Optional[int] = None,
        since: Optional[str] = None,
    ) -> int:
        rows = self.fold(scope_id=scope_id)
        if seq is not None:
            return sum(1 for event in rows if (event.seq or 0) > seq)
        if since is not None:
            return sum(1 for event in rows if (event.occurred_at or "") > since)
        return len(rows)

    # test affordances
    def all(self) -> list[Event]:
        return list(self._events)


class MemoryProposalBox:
    """Pending proposals identified by (scope, key, evidence digest)."""

    def __init__(self) -> None:
        self._rows: dict[int, Proposal] = {}
        self._next_id = 1

    def find_claim(
        self, *, scope_id: str, external_key: str, evidence_digest: str
    ) -> Optional[Proposal]:
        for proposal in self._rows.values():
            if (
                proposal.scope_id == scope_id
                and proposal.external_key == external_key
                and proposal.evidence_digest == evidence_digest
                and proposal.state == "pending"
            ):
                return proposal
        return None

    def insert(self, proposal: Proposal) -> Proposal:
        stored = replace(
            proposal,
            proposal_id=self._next_id,
            sources=tuple(proposal.submitters),
        )
        self._rows[self._next_id] = stored
        self._next_id += 1
        return stored

    def merge_submitters(
        self, *, proposal_id: int, submitters: Sequence[str]
    ) -> Proposal:
        current = self._rows[proposal_id]
        merged = tuple(dict.fromkeys([*current.submitters, *submitters]))
        updated = replace(current, sources=merged)
        self._rows[proposal_id] = updated
        return updated

    def find_by_key(
        self, *, scope_id: str, external_key: str, state: Optional[str] = None
    ) -> Optional[Proposal]:
        for proposal in sorted(self._rows.values(), key=lambda p: p.proposal_id or 0, reverse=True):
            if proposal.scope_id != scope_id or proposal.external_key != external_key:
                continue
            if state is not None and proposal.state != state:
                continue
            return proposal
        return None

    def get(self, *, scope_id: str, proposal_id: int) -> Optional[Proposal]:
        proposal = self._rows.get(proposal_id)
        if proposal is None or proposal.scope_id != scope_id:
            return None
        return proposal

    def decide(
        self, *, proposal_id: int, state: str, decided_by: str, note: str = ""
    ) -> Proposal:
        updated = replace(
            self._rows[proposal_id],
            state=state,
            decided_by=decided_by,
            decision_note=note,
        )
        self._rows[proposal_id] = updated
        return updated

    def list_pending(
        self, *, scope_id: str, batch: Optional[str] = None, limit: int = 200
    ) -> list[Proposal]:
        rows = [
            proposal
            for proposal in self._rows.values()
            if proposal.scope_id == scope_id and proposal.state == "pending"
        ]
        return sorted(rows, key=lambda p: p.proposal_id or 0)[:limit]

    # test affordances
    def count(self) -> int:
        return len(self._rows)


class MemoryPublicationStore:
    """Immutable snapshots with a monotonic per-scope version."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, int], Publication] = {}

    def latest(self, *, scope_id: str) -> Optional[Publication]:
        versions = [v for (scope, v) in self._rows if scope == scope_id]
        if not versions:
            return None
        return self._rows[(scope_id, max(versions))]

    def get(
        self,
        *,
        scope_id: str,
        version: Optional[int] = None,
        digest: Optional[str] = None,
    ) -> Optional[Publication]:
        if version is not None:
            return self._rows.get((scope_id, version))
        if digest is None:
            raise ValueError(
                "get needs a version or a digest; use latest() for the newest version"
            )
        for (scope, _v), publication in self._rows.items():
            if scope == scope_id and publication.digest == digest:
                return publication
        return None

    def next_version(self, *, scope_id: str) -> int:
        versions = [v for (scope, v) in self._rows if scope == scope_id]
        return (max(versions) + 1) if versions else 1

    def insert(self, publication: Publication) -> Publication:
        key = (publication.scope_id, publication.version)
        if key in self._rows:
            raise ValueError(f"publication {key} already exists and is immutable")
        self._rows[key] = publication
        return publication

    # test affordances
    def count(self) -> int:
        return len(self._rows)

    def all(self) -> list[Publication]:
        return sorted(self._rows.values(), key=lambda p: (p.scope_id, p.version))


class StaticVocabulary:
    """A vocabulary per scope, supplied up front (an enum or a loaded file)."""

    def __init__(self, vocabulary: Vocabulary, *, scopes: Optional[Sequence[str]] = None) -> None:
        self._vocabulary = vocabulary
        self._scopes = set(scopes) if scopes is not None else None

    def load(self, *, scope_id: str) -> Vocabulary:
        if self._scopes is not None and scope_id not in self._scopes:
            raise KeyError(f"no vocabulary for scope {scope_id!r}")
        return self._vocabulary

    # test affordance: a source that records how often it was asked
    calls: int = 0


class MemoryRegistry:
    """Consumers keyed by token digest, plus an append-only pull trail."""

    def __init__(self) -> None:
        self._consumers: dict[int, Consumer] = {}
        self._pulls: list[PullEvent] = []
        self._next_id = 1

    def register(
        self, *, name: str, scopes: Sequence[str], token_hash: str, created_by: str
    ) -> Consumer:
        consumer = Consumer(
            consumer_id=self._next_id,
            name=name,
            scopes=tuple(scopes),
            token_hash=token_hash,
            created_by=created_by,
        )
        self._consumers[self._next_id] = consumer
        self._next_id += 1
        return consumer

    def find_by_token_hash(self, *, token_hash: str) -> Optional[Consumer]:
        for consumer in self._consumers.values():
            if consumer.token_hash == token_hash:
                return consumer
        return None

    def record_pull(self, *, consumer_id: int, scope_id: str, version: int) -> PullEvent:
        event = PullEvent(
            consumer_id=consumer_id,
            scope_id=scope_id,
            version=version,
            happened_at=f"t{len(self._pulls) + 1:06d}",
        )
        self._pulls.append(event)
        return event

    def pulls_since(
        self, *, consumer_id: Optional[int] = None, since: Optional[str] = None
    ) -> list[PullEvent]:
        rows = [
            event for event in self._pulls
            if consumer_id is None or event.consumer_id == consumer_id
        ]
        if since is not None:
            rows = [event for event in rows if (event.happened_at or "") > since]
        return rows

    def consumers(self) -> list[Consumer]:
        return sorted(self._consumers.values(), key=lambda c: c.consumer_id)

    def revoke(self, *, consumer_id: int) -> Consumer:
        updated = replace(self._consumers[consumer_id], revoked_at="t-revoked")
        self._consumers[consumer_id] = updated
        return updated


def memory_vocabulary(
    *,
    release: str = "test.1",
    entity_types: Sequence[str] = ("entity", "clause", "law", "concept"),
    relationship_kinds: Sequence[str] = ("belongs_to", "cites_law", "defines"),
) -> Vocabulary:
    """The vocabulary the kernel tests and the worked example both use."""
    return Vocabulary.from_values(
        release=release,
        values={
            "entity_types": entity_types,
            "relationship_kinds": relationship_kinds,
        },
    )


def memory_kernel(**kwargs: Any):
    """A kernel wired to fresh memory ports (handy in tests and examples)."""
    from .kernel import Kernel

    units = MemoryUnitStore()
    journal = MemoryJournal()
    box = MemoryProposalBox()
    publications = MemoryPublicationStore()
    registry = MemoryRegistry()
    vocabulary = kwargs.pop("vocabulary", None) or StaticVocabulary(memory_vocabulary())
    return Kernel(
        units=units,
        events=journal,
        vocabulary=vocabulary,
        proposals=box,
        publications=publications,
        distribution=registry,
        relationship_kinds=("belongs_to", "cites_law", "defines"),
        **kwargs,
    )
