"""The six ports every ontology surface implements.

The kernel owns mechanisms, not storage. Each surface supplies these six
objects; the kernel calls them and never opens a connection, reads a config
file, or makes a request of its own.

Why ports instead of a shared ORM base: the surfaces disagree on everything
below the semantics — one is psycopg over PostgreSQL with JSONB, one is
SQLAlchemy over both SQLite and PostgreSQL, one is a YAML file. A shared model
layer would either drag SQLAlchemy into the first or dissolve into guesswork.
The invariants, on the other hand, are identical, and those are what this
package holds.

Transaction discipline: ``Kernel.write()`` calls ``UnitStore.upsert`` and
``EventJournal.append`` in that order, once. The adapter passes both ports a
handle on the *same* transaction (``conn.transaction()``, ``Session.begin()``).
The kernel cannot open or close a transaction — it only requires that the pair
lands together.
"""

from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from .contracts import Consumer, Event, Proposal, Publication, PullEvent, Unit
from .vocab import Vocabulary


@runtime_checkable
class UnitStore(Protocol):
    """Instance storage: get one, upsert one, list what is live."""

    def get(self, *, scope_id: str, kind: str, external_key: str) -> Optional[Unit]:
        ...

    def upsert(self, unit: Unit) -> Unit:
        ...

    def list_live(self, *, scope_id: str) -> list[Unit]:
        ...

    def live_key_kinds(self, *, scope_id: str) -> dict[str, str]:
        """Every live external key mapped to its kind (relationship endpoint check)."""
        ...


@runtime_checkable
class EventJournal(Protocol):
    """Append-only change trail. No update or delete method exists, by design."""

    def append(self, event: Event) -> Event:
        ...

    def fold(self, *, scope_id: str) -> list[Event]:
        """Events in canonical order (by ``seq`` when present, else by time)."""
        ...

    def count_since(
        self,
        *,
        scope_id: str,
        seq: Optional[int] = None,
        since: Optional[str] = None,
    ) -> int:
        """Unpublished-change count: events after a seq or a timestamp."""
        ...


@runtime_checkable
class ProposalBox(Protocol):
    """Proposal storage: identity lookup, insert, and a decision."""

    def find_claim(
        self, *, scope_id: str, external_key: str, evidence_digest: str
    ) -> Optional[Proposal]:
        """The pending proposal with this exact claim, if any."""
        ...

    def insert(self, proposal: Proposal) -> Proposal:
        ...

    def merge_submitters(
        self, *, proposal_id: int, submitters: Sequence[str]
    ) -> Proposal:
        ...

    def find_by_key(
        self, *, scope_id: str, external_key: str, state: Optional[str] = None
    ) -> Optional[Proposal]:
        ...

    def get(self, *, scope_id: str, proposal_id: int) -> Optional[Proposal]:
        ...

    def decide(
        self, *, proposal_id: int, state: str, decided_by: str, note: str = ""
    ) -> Proposal:
        ...

    def list_pending(
        self, *, scope_id: str, batch: Optional[str] = None, limit: int = 200
    ) -> list[Proposal]:
        ...


@runtime_checkable
class PublicationStore(Protocol):
    """Immutable snapshots. Insert-only; rows are never rewritten."""

    def latest(self, *, scope_id: str) -> Optional[Publication]:
        ...

    def get(
        self,
        *,
        scope_id: str,
        version: Optional[int] = None,
        digest: Optional[str] = None,
    ) -> Optional[Publication]:
        ...

    def next_version(self, *, scope_id: str) -> int:
        ...

    def insert(self, publication: Publication) -> Publication:
        ...


@runtime_checkable
class VocabularySource(Protocol):
    """Where a scope's closed vocabularies come from (row, enum, or YAML)."""

    def load(self, *, scope_id: str) -> Vocabulary:
        ...


@runtime_checkable
class DistributionRegistry(Protocol):
    """Consumers and their pull trail. The trail is append-only."""

    def find_by_token_hash(self, *, token_hash: str) -> Optional[Consumer]:
        ...

    def record_pull(self, *, consumer_id: int, scope_id: str, version: int) -> PullEvent:
        ...

    def pulls_since(
        self, *, consumer_id: Optional[int] = None, since: Optional[str] = None
    ) -> list[PullEvent]:
        ...

    def consumers(self) -> list[Consumer]:
        ...


#: Port name -> protocol, used by ``Kernel`` to name what is missing and by the
#: conformance suite to check an adapter implements the set it claims.
PORTS: dict[str, type] = {
    "units": UnitStore,
    "events": EventJournal,
    "proposals": ProposalBox,
    "publications": PublicationStore,
    "vocabulary": VocabularySource,
    "distribution": DistributionRegistry,
}

#: Ports without which the write path cannot run at all.
REQUIRED_PORTS: tuple[str, ...] = ("units", "events", "vocabulary")
