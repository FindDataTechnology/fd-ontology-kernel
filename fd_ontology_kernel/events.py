"""Append-only change trail: the envelope, the fold, and the unpublished count.

An ontology without a change trail cannot answer "what happened to this entity"
or "what changed since the last published version". Both surfaces therefore
append one row per write, in the same transaction as the write.

What this module fixes is the *envelope*, not the storage: op vocabulary, the
create/update/delete invariants (a create has no before state, a delete no after
state), the portability of addressing (``target_key`` always; ``target_ref``
when the surface has a surrogate id), and folding a trail into current state.

Mutation is impossible by construction: :class:`~fd_ontology_kernel.contracts.Event`
is frozen, and there is no update or delete function here to call.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from .contracts import EVENT_OPS, Event
from .errors import EventAppendOnly
from .ports import EventJournal


def build_event(
    *,
    scope_id: str,
    op: str,
    target_kind: str,
    target_key: str,
    actor: str,
    via: str,
    before: Optional[Mapping[str, Any]] = None,
    after: Optional[Mapping[str, Any]] = None,
    target_ref: Optional[Any] = None,
    seq: Optional[int] = None,
    source_ref: Optional[str] = None,
    source_instance_id: Optional[int] = None,
    occurred_at: Optional[str] = None,
) -> Event:
    """Construct and validate one event (validation lives in the dataclass)."""
    if op not in EVENT_OPS:
        raise EventAppendOnly(
            f"op {op!r} is not one of {EVENT_OPS}; the trail records content "
            "changes only",
            op=op,
            allowed=list(EVENT_OPS),
        )
    return Event(
        scope_id=scope_id,
        op=op,
        target_kind=target_kind,
        target_key=target_key,
        actor=actor,
        via=via,
        before=before,
        after=after,
        target_ref=target_ref,
        seq=seq,
        source_ref=source_ref,
        source_instance_id=source_instance_id,
        occurred_at=occurred_at,
    )


def append(journal: EventJournal, event: Event) -> Event:
    """Append through the port. The journal is the only writer."""
    return journal.append(event)


def attempt_mutation(event: Event, **changes: Any) -> None:
    """Always raises: kept so an adapter that offers a "fix event" path can call
    this and get the same refusal the kernel would give.

    ``dataclasses.replace`` would also create a *copy* rather than mutate, but a
    copy with a different payload appended under the same identity is exactly the
    rewrite this invariant exists to prevent.
    """
    raise EventAppendOnly(
        "events are append-only: there is no path that rewrites an appended event",
        target_key=event.target_key,
        attempted=list(changes),
    )


def _order(events: Sequence[Event]) -> list[Event]:
    """Canonical order: by ``seq`` where the surface has one, else by time.

    Mixing the two would be wrong, so an event list that has *any* seq is ordered
    by seq with missing seq last (an adapter that journals by key must supply a
    stable arrival order instead).
    """
    has_seq = any(e.seq is not None for e in events)

    def key(event: Event) -> tuple:
        if has_seq:
            return (0 if event.seq is not None else 1, event.seq or 0,
                    event.occurred_at or "")
        return (0, 0, event.occurred_at or "")

    return sorted(events, key=key)


def fold(events: Sequence[Event]) -> dict[str, Optional[dict[str, Any]]]:
    """Replay a trail into current state, keyed by ``target_key``.

    ``None`` marks a deleted target (the key is kept so a later create under the
    same key is visibly a re-creation, not a first appearance).
    """
    state: dict[str, Optional[dict[str, Any]]] = {}
    for event in _order(events):
        if event.op == "delete":
            state[event.target_key] = None
        else:
            state[event.target_key] = dict(event.after or {})
    return state


def live_keys(events: Sequence[Event]) -> set[str]:
    """Keys whose latest event is not a delete."""
    return {key for key, value in fold(events).items() if value is not None}


def unpublished_count(
    journal: EventJournal,
    *,
    scope_id: str,
    last_seq: Optional[int] = None,
    last_published_at: Optional[str] = None,
) -> int:
    """Events after the last publication (by seq when available, else by time)."""
    return journal.count_since(
        scope_id=scope_id, seq=last_seq, since=last_published_at
    )
