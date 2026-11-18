"""A worked example: the six ports over one JSON file.

This adapter exists to be **copied**. It implements every port against the
smallest storage that still shows the shape of the work — a single JSON file —
so nothing here depends on the kernel's in-memory helpers or on a database.

Read it top to bottom and you have the whole surface an adapter must provide:

* ``ToyUnitStore``        — get / upsert / list_live / live_key_kinds (+ ``raw_count``)
* ``ToyJournal``          — append / fold / count_since (+ ``next_seq``)
* ``ToyProposalBox``      — the seven proposal methods
* ``ToyPublicationStore`` — latest / get / next_version / insert (+ ``count``)
* ``ToyVocabulary``       — the closed sets this system allows
* ``ToyRegistry``         — consumers and their pull trail

Two things are easy to get wrong, so they are spelled out here:

1. **The journal is append-only.** There is no update or delete method, and the
   kernel refuses to construct a mutable event. Do not add one "just for tests".
2. **The transaction is the adapter's job.** The kernel calls ``upsert`` then
   ``append`` once and expects both to land or neither to. A file store has no
   transaction, so this example writes both through one ``_flush()`` call; a SQL
   adapter wraps the pair in one transaction instead.

Run the gate against it::

    python scripts/ontology_conformance_gate.py --adapter toy
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from fd_ontology_kernel.contracts import (
    Consumer,
    Event,
    Proposal,
    Publication,
    PullEvent,
    Unit,
)
from fd_ontology_kernel.kernel import AdapterDeclaration, Kernel
from fd_ontology_kernel.vocab import Vocabulary

#: What this system claims to speak. ``manifest_shape`` is about how the payload
#: addresses entries: 1 = by surrogate id, 2 = by semantic key.
DECLARATION = AdapterDeclaration(
    system="toy-json",
    kernel_contract="1.0",
    manifest_shape=2,
)

#: The closed sets. Anything not listed here is refused before it is written.
ENTITY_TYPES: tuple[str, ...] = ("entity", "clause")
RELATIONSHIP_KINDS: tuple[str, ...] = ("belongs_to", "cites")


class _Store:
    """One JSON file as the whole database. Replace with your real store."""

    def __init__(self, path: Path) -> None:
        self.path = path
        if not path.exists():
            path.write_text(
                json.dumps({"units": {}, "events": [], "publications": []}),
                encoding="utf-8",
            )

    def read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, data: Mapping[str, Any]) -> None:
        # One write call = one "transaction" here. A SQL adapter would not need
        # this: it would hand both ports the same open transaction instead.
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )


class ToyUnitStore:
    def __init__(self, store: _Store) -> None:
        self._store = store

    @staticmethod
    def _key(scope_id: str, kind: str, external_key: str) -> str:
        return f"{scope_id}\u0000{kind}\u0000{external_key}"

    def get(self, *, scope_id: str, kind: str, external_key: str) -> Unit | None:
        row = self._store.read()["units"].get(self._key(scope_id, kind, external_key))
        return Unit(**row) if row else None

    def upsert(self, unit: Unit) -> Unit:
        data = self._store.read()
        data["units"][self._key(unit.scope_id, unit.kind, unit.external_key)] = {
            "scope_id": unit.scope_id,
            "kind": unit.kind,
            "external_key": unit.external_key,
            "body": unit.body,
            "tier": unit.tier,
            "tags": dict(unit.tags),
            "attrs": dict(unit.attrs),
            "source_path": unit.source_path,
            "provenance": dict(unit.provenance),
        }
        self._store.write(data)
        return unit

    def list_live(self, *, scope_id: str) -> list[Unit]:
        rows = [
            Unit(**row)
            for row in self._store.read()["units"].values()
            if row["scope_id"] == scope_id
        ]
        return sorted(rows, key=lambda unit: (unit.kind, unit.external_key))

    def live_key_kinds(self, *, scope_id: str) -> dict[str, str]:
        return {unit.external_key: unit.kind for unit in self.list_live(scope_id=scope_id)}

    def raw_count(self) -> int:
        return len(self._store.read()["units"])


class ToyJournal:
    """Append-only. There is deliberately no way to change or remove an event."""

    def __init__(self, store: _Store) -> None:
        self._store = store

    def next_seq(self, scope_id: str) -> int:
        seqs = [
            int(row["seq"])
            for row in self._store.read()["events"]
            if row["scope_id"] == scope_id and row.get("seq") is not None
        ]
        return max(seqs, default=0) + 1

    def append(self, event: Event) -> Event:
        data = self._store.read()
        data["events"].append(
            {
                "scope_id": event.scope_id,
                "op": event.op,
                "target_kind": event.target_kind,
                "target_key": event.target_key,
                "actor": event.actor,
                "via": event.via,
                "before": dict(event.before) if event.before is not None else None,
                "after": dict(event.after) if event.after is not None else None,
                "target_ref": event.target_ref,
                "seq": event.seq,
                "source_ref": event.source_ref,
                "source_instance_id": event.source_instance_id,
                "occurred_at": event.occurred_at,
            }
        )
        self._store.write(data)
        return event

    def fold(self, *, scope_id: str) -> list[Event]:
        rows = [
            Event(**row)
            for row in self._store.read()["events"]
            if row["scope_id"] == scope_id
        ]
        return sorted(rows, key=lambda event: (event.seq or 0, event.target_key))

    def count_since(
        self, *, scope_id: str, seq: int | None = None, since: str | None = None
    ) -> int:
        return len(
            [
                event
                for event in self.fold(scope_id=scope_id)
                if seq is None or (event.seq or 0) > int(seq)
            ]
        )


class ToyProposalBox:
    """Nominations live here until a human decides. Nothing writes to units."""

    def __init__(self) -> None:
        self._rows: dict[int, Proposal] = {}
        self._next_id = 1

    def find_claim(
        self, *, scope_id: str, external_key: str, evidence_digest: str
    ) -> Proposal | None:
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
        stored = replace(proposal, proposal_id=self._next_id)
        self._rows[self._next_id] = stored
        self._next_id += 1
        return stored

    def merge_submitters(
        self, *, proposal_id: int, submitters: Sequence[str]
    ) -> Proposal:
        stored = replace(self._rows[int(proposal_id)], sources=tuple(submitters))
        self._rows[int(proposal_id)] = stored
        return stored

    def find_by_key(
        self, *, scope_id: str, external_key: str, state: str | None = None
    ) -> Proposal | None:
        for proposal in self._rows.values():
            if proposal.scope_id != scope_id or proposal.external_key != external_key:
                continue
            if state is None or proposal.state == state:
                return proposal
        return None

    def get(self, *, scope_id: str, proposal_id: int) -> Proposal | None:
        proposal = self._rows.get(int(proposal_id))
        return proposal if proposal and proposal.scope_id == scope_id else None

    def decide(
        self, *, proposal_id: int, state: str, decided_by: str, note: str = ""
    ) -> Proposal:
        stored = replace(
            self._rows[int(proposal_id)],
            state=state,
            decided_by=decided_by,
            decision_note=note,
        )
        self._rows[int(proposal_id)] = stored
        return stored

    def list_pending(
        self, *, scope_id: str, batch: str | None = None, limit: int = 200
    ) -> list[Proposal]:
        rows = [
            proposal
            for proposal in self._rows.values()
            if proposal.scope_id == scope_id and proposal.state == "pending"
        ]
        return sorted(rows, key=lambda proposal: proposal.proposal_id or 0)[: int(limit)]


class ToyPublicationStore:
    def __init__(self, store: _Store) -> None:
        self._store = store

    def _rows(self, scope_id: str | None = None) -> list[dict[str, Any]]:
        rows = self._store.read()["publications"]
        if scope_id is None:
            return rows
        return [row for row in rows if row["scope_id"] == scope_id]

    def latest(self, *, scope_id: str) -> Publication | None:
        rows = self._rows(scope_id)
        return Publication(**max(rows, key=lambda row: row["version"])) if rows else None

    def get(
        self,
        *,
        scope_id: str,
        version: int | None = None,
        digest: str | None = None,
    ) -> Publication | None:
        for row in self._rows(scope_id):
            if version is not None and int(row["version"]) != int(version):
                continue
            if digest is not None and row["digest"] != digest:
                continue
            return Publication(**row)
        return None

    def next_version(self, *, scope_id: str) -> int:
        rows = self._rows(scope_id)
        return max((int(row["version"]) for row in rows), default=0) + 1

    def insert(self, publication: Publication) -> Publication:
        data = self._store.read()
        data["publications"].append(
            {
                "scope_id": publication.scope_id,
                "version": publication.version,
                "digest": publication.digest,
                "payload": dict(publication.payload),
                "manifest_schema_version": publication.manifest_schema_version,
                "trigger": publication.trigger,
                "label": publication.label,
                "created_by": publication.created_by,
                "created_at": publication.created_at,
            }
        )
        self._store.write(data)
        return publication

    def count(self) -> int:
        return len(self._rows())


class ToyVocabulary:
    def load(self, *, scope_id: str) -> Vocabulary:
        return Vocabulary.from_values(
            release="toy-1",
            values={
                "entity_types": ENTITY_TYPES,
                "relationship_kinds": RELATIONSHIP_KINDS,
            },
        )


class ToyRegistry:
    """Consumers plus an append-only pull trail."""

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

    def find_by_token_hash(self, *, token_hash: str) -> Consumer | None:
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
        self, *, consumer_id: int | None = None, since: str | None = None
    ) -> list[PullEvent]:
        rows = [
            event
            for event in self._pulls
            if consumer_id is None or event.consumer_id == consumer_id
        ]
        if since is not None:
            rows = [event for event in rows if (event.happened_at or "") > since]
        return rows

    def consumers(self) -> list[Consumer]:
        return sorted(self._consumers.values(), key=lambda c: c.consumer_id)


def _sandbox() -> Kernel:
    """A fresh store per call: the gate must never touch data it did not create."""
    import tempfile

    directory = tempfile.mkdtemp(prefix="toy-ontology-")
    store = _Store(Path(directory) / "toy.json")
    return Kernel(
        units=ToyUnitStore(store),
        events=ToyJournal(store),
        vocabulary=ToyVocabulary(),
        proposals=ToyProposalBox(),
        publications=ToyPublicationStore(store),
        distribution=ToyRegistry(),
        relationship_kinds=RELATIONSHIP_KINDS,
        clause_kinds=("clause",),
        manifest_shape=DECLARATION.manifest_shape,
        declaration=DECLARATION,
    )


def build():
    """Factory named by ``adapters.json``."""
    from fd_ontology_kernel.conformance.spec import AdapterSpec

    return AdapterSpec(
        name="toy",
        system=DECLARATION.system,
        declaration=DECLARATION,
        sandbox=_sandbox,
        notes="worked example: all six ports over one JSON file, every capability present",
    )
