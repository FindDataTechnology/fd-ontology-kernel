"""The four-layer ledger, computed as a pure projection.

The ledger answers "what is declared, what is curated, what is published, and who
has consumed it". It owns no table: every layer is derived from the same four
authoritative stores on demand, so the page can never disagree with the machine
surface, and opening the ledger writes nothing.

The two facts that must stay distinct and visible are **declared-but-empty** and
**smoke leftovers**. A vocabulary type with zero instances is a coverage gap; a
`smoke` key is junk to clean. Rendering them as one list invites "just delete the
gaps", which is the opposite of the truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from .contracts import Consumer, Publication, PullEvent, Unit
from .vocab import Vocabulary

#: Markers that identify smoke-test content. Used to *label* rows only.
SMOKE_MARKERS: tuple[str, ...] = ("smoke", "冒烟")


def is_smoke(unit: Unit) -> bool:
    haystack = f"{unit.external_key} {unit.body}".lower()
    return any(marker in haystack for marker in SMOKE_MARKERS)


@dataclass(frozen=True)
class Ledger:
    """The four layers plus the gap and consistency facts."""

    declaration: Mapping[str, Any]
    curation: Mapping[str, Any]
    publication: Mapping[str, Any]
    consumption: Mapping[str, Any]
    gaps: Mapping[str, Any] = field(default_factory=dict)
    smoke: Sequence[Mapping[str, Any]] = ()
    reconcile: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "declaration": dict(self.declaration),
            "curation": dict(self.curation),
            "publication": dict(self.publication),
            "consumption": dict(self.consumption),
            "gaps": dict(self.gaps),
            "smoke": list(self.smoke),
            "reconcile": dict(self.reconcile),
        }


def project(
    *,
    vocabulary: Vocabulary,
    units: Sequence[Unit],
    publication: Optional[Publication] = None,
    consumers: Sequence[Consumer] = (),
    pulls: Sequence[PullEvent] = (),
    relationship_kinds: Sequence[str] = (),
    unpublished_changes: int = 0,
) -> Ledger:
    """Build the ledger from already-read facts. Reads nothing, writes nothing."""
    rel_kinds = set(relationship_kinds)
    live = [u for u in units if not (u.tags or {}).get("deleted")]

    declared_types = list(vocabulary.sets.get("entity_types").values) if "entity_types" in vocabulary.sets else []
    declared_rels = list(vocabulary.sets.get("relationship_kinds").values) if "relationship_kinds" in vocabulary.sets else []

    by_kind: dict[str, int] = {}
    for unit in live:
        by_kind[unit.kind] = by_kind.get(unit.kind, 0) + 1

    out_of_vocabulary = sorted(
        kind for kind in by_kind
        if not vocabulary.has("entity_types", kind) and not vocabulary.has("relationship_kinds", kind)
    )

    gaps = [kind for kind in declared_types if by_kind.get(kind, 0) == 0]
    rel_gaps = [kind for kind in declared_rels if by_kind.get(kind, 0) == 0]

    latest_pull: dict[int, PullEvent] = {}
    pull_counts: dict[int, int] = {}
    for event in pulls:
        pull_counts[event.consumer_id] = pull_counts.get(event.consumer_id, 0) + 1
        current = latest_pull.get(event.consumer_id)
        if current is None or (event.happened_at or "") >= (current.happened_at or ""):
            latest_pull[event.consumer_id] = event

    consumption_rows = [
        {
            "name": consumer.name,
            "scopes": list(consumer.scopes),
            "revoked": consumer.revoked,
            "pulls": pull_counts.get(consumer.consumer_id, 0),
            "last_pull": (
                latest_pull[consumer.consumer_id].happened_at
                if consumer.consumer_id in latest_pull else None
            ),
            "last_version": (
                latest_pull[consumer.consumer_id].version
                if consumer.consumer_id in latest_pull else None
            ),
        }
        for consumer in consumers
    ]

    return Ledger(
        declaration={
            "release": vocabulary.release,
            "entity_types": declared_types,
            "relationship_kinds": declared_rels,
            "sets": {name: list(hold.values) for name, hold in vocabulary.sets.items()},
        },
        curation={
            "total": len(live),
            "entities": sum(1 for u in live if u.kind not in rel_kinds),
            "relationships": sum(1 for u in live if u.kind in rel_kinds),
            "by_kind": [
                {"kind": kind, "count": by_kind[kind]} for kind in sorted(by_kind)
            ],
        },
        publication={
            "latest_version": publication.version if publication else None,
            "latest_digest": publication.digest if publication else None,
            "latest_at": publication.created_at if publication else None,
            "manifest_schema_version": publication.manifest_schema_version if publication else None,
        },
        consumption={
            "consumers": consumption_rows,
            "never_pulled": [
                row["name"] for row in consumption_rows
                if row["pulls"] == 0 and not row["revoked"]
            ],
        },
        gaps={
            "entity_types": gaps,
            "relationship_kinds": rel_gaps,
        },
        smoke=[
            {"kind": unit.kind, "external_key": unit.external_key}
            for unit in live
            if is_smoke(unit)
        ],
        reconcile={
            "unused_entity_types": gaps,
            "unused_relationship_kinds": rel_gaps,
            "out_of_vocabulary": out_of_vocabulary,
            "unpublished_changes": unpublished_changes,
            "never_pulled_consumers": [
                row["name"] for row in consumption_rows
                if row["pulls"] == 0 and not row["revoked"]
            ],
            "smoke_leftovers": sum(1 for unit in live if is_smoke(unit)),
        },
    )
