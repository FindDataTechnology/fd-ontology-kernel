"""Events: envelope validation, append, fold, and the absence of rewrite."""

from __future__ import annotations

import dataclasses

import pytest

from fd_ontology_kernel import events as events_mod
from fd_ontology_kernel.contracts import Event
from fd_ontology_kernel.errors import EventAppendOnly
from fd_ontology_kernel.memory import MemoryJournal


def _event(**overrides):
    base = dict(
        scope_id="d1", op="create", target_kind="law", target_key="合同法",
        actor="u1", via="test", after={"body": "合同法"},
    )
    base.update(overrides)
    if base["op"] == "delete":
        base.pop("after", None)
    if base["op"] == "create":
        base.pop("before", None)
    return events_mod.build_event(**base)


def test_build_event_rejects_an_unknown_op() -> None:
    with pytest.raises(EventAppendOnly):
        _event(op="upsert")


def test_append_assigns_a_seq_and_a_timestamp_when_absent() -> None:
    journal = MemoryJournal()
    stored = events_mod.append(journal, _event())
    assert stored.seq == 1
    assert stored.occurred_at


def test_append_keeps_a_supplied_seq() -> None:
    journal = MemoryJournal()
    stored = events_mod.append(journal, _event(seq=7))
    assert stored.seq == 7
    assert journal.count_since(scope_id="d1", seq=6) == 1


def test_fold_replays_to_current_state_and_marks_deletes() -> None:
    journal = MemoryJournal()
    events_mod.append(journal, _event(after={"body": "v1"}))
    events_mod.append(journal, _event(op="update", before={"body": "v1"}, after={"body": "v2"}))
    events_mod.append(journal, _event(op="delete", before={"body": "v2"}))
    state = events_mod.fold(journal.fold(scope_id="d1"))
    assert state == {"合同法": None}
    assert events_mod.live_keys(journal.fold(scope_id="d1")) == set()


def test_fold_is_scoped() -> None:
    journal = MemoryJournal()
    events_mod.append(journal, _event(scope_id="d1"))
    events_mod.append(journal, _event(scope_id="d2", target_key="劳动法"))
    state = events_mod.fold(journal.fold(scope_id="d2"))
    assert set(state) == {"劳动法"}


def test_fold_orders_by_time_when_the_surface_keeps_no_seq() -> None:
    """A key-addressed surface has no seq; arrival order must still be respected."""
    early = _event(seq=None, occurred_at="2026-11-01T00:00:00", after={"body": "v1"})
    late = _event(op="update", seq=None, occurred_at="2026-11-02T00:00:00",
                  before={"body": "v1"}, after={"body": "v2"})
    # Deliberately out of order: the fold must sort, not trust the list.
    state = events_mod.fold([late, early])
    assert state["合同法"] == {"body": "v2"}


def test_count_since_by_seq_and_by_time() -> None:
    journal = MemoryJournal()
    first = events_mod.append(journal, _event())
    events_mod.append(journal, _event(target_key="劳动法"))
    assert journal.count_since(scope_id="d1", seq=first.seq) == 1
    assert journal.count_since(scope_id="d1", since=first.occurred_at) == 1
    assert journal.count_since(scope_id="d1") == 2


def test_attempt_mutation_is_refused_with_a_code() -> None:
    event = _event()
    with pytest.raises(EventAppendOnly) as caught:
        events_mod.attempt_mutation(event, after={"body": "rewritten"})
    assert caught.value.context["target_key"] == "合同法"


def test_replace_would_not_mutate_but_the_journal_is_the_only_writer() -> None:
    """A copy is not a mutation — but nothing appends a copy over an identity."""
    event = _event()
    copy = dataclasses.replace(event, after={"body": "other"})
    assert event.after == {"body": "合同法"}
    assert copy is not event
