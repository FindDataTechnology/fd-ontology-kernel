"""Closed vocabularies and the additive-only rule.

A vocabulary is the set of kinds a scope is allowed to write. Three surfaces
keep it in three places — a database row's JSONB, a code-level enum, a YAML
file — and this module is the one place that decides what "closed" and "additive"
mean, so all three behave identically.

Two disciplines, both learned the hard way:

* **Closed** — a value outside the set is refused *before* anything is written,
  and the error lists the allowed values. Refusing late means an instance with a
  bad kind is already in the store.
* **Additive** — a release may add entries, retire them with ``superseded_by``,
  but never delete one or change an id. A deletion silently orphans every
  instance that used the value, and there is no way back because the old
  meaning is gone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from .errors import VocabEntryRemoved, VocabOutOfSet

#: Base for entry URIs. Stable forever; consumers pin it.
URI_BASE = "https://schema.finddata.tech"


def uri_for(kind: str, entry_id: str) -> str:
    """``https://schema.finddata.tech/<kind>/<id>`` — one constructor, one result.

    Every system must derive the same URI from the same identifier; a second
    implementation is how two systems end up describing the same concept with
    two URIs.
    """
    kind_part = (kind or "").strip().strip("/")
    id_part = str(entry_id or "").strip()
    if not kind_part or not id_part:
        raise ValueError("uri_for needs both a kind and an entry id")
    return f"{URI_BASE}/{kind_part}/{id_part}"


@dataclass(frozen=True)
class Entry:
    """One vocabulary entry, with the labels a UI or a prompt may show."""

    id: str
    set_name: str
    label_en: Optional[str] = None
    label_zh: Optional[str] = None
    description: Optional[str] = None
    #: The successor entry, when this one is retired. Never a deletion.
    superseded_by: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def uri(self) -> str:
        return uri_for(self.set_name, self.id)

    @property
    def retired(self) -> bool:
        return bool(self.superseded_by)


@dataclass(frozen=True)
class VocabularySet:
    """One closed set of values (e.g. ``entity_types``)."""

    name: str
    values: tuple[str, ...]
    entries: Mapping[str, Entry] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a vocabulary set needs a name")
        # An empty set is legitimate: a registry may declare a kind family it has
        # no entries for yet (the protocol's ``event_types.yaml`` does exactly
        # that). The closed-set semantics still hold — every value is refused,
        # with an empty allowed list — and an additive release that empties a
        # previously populated set is caught as removed entries.

    def has(self, value: str) -> bool:
        return value in self.values

    def entry(self, value: str) -> Optional[Entry]:
        return self.entries.get(value)

    def require(self, value: str, *, scope_id: str = "") -> None:
        """Refuse a value outside the set, naming the set and its contents."""
        if not self.has(value):
            raise VocabOutOfSet(
                f"value {value!r} is not in the {self.name!r} vocabulary; "
                f"allowed kinds: {list(self.values)}",
                set_name=self.name,
                value=value,
                allowed=list(self.values),
                scope_id=scope_id,
            )


@dataclass(frozen=True)
class Vocabulary:
    """A scope's whole vocabulary: several named closed sets plus a release."""

    release: str
    sets: Mapping[str, VocabularySet]

    def __post_init__(self) -> None:
        if not self.release:
            raise ValueError("a vocabulary needs a release identifier")
        if not self.sets:
            raise ValueError("a vocabulary needs at least one set")

    def set(self, name: str) -> VocabularySet:
        try:
            return self.sets[name]
        except KeyError:
            raise VocabOutOfSet(
                f"vocabulary has no set named {name!r}; available: {sorted(self.sets)}",
                set_name=name,
                allowed=sorted(self.sets),
            ) from None

    def has(self, set_name: str, value: str) -> bool:
        hold = self.sets.get(set_name)
        return bool(hold and hold.has(value))

    def require(self, set_name: str, value: str, *, scope_id: str = "") -> None:
        self.set(set_name).require(value, scope_id=scope_id)

    def uris(self) -> list[str]:
        return [entry.uri for hold in self.sets.values() for entry in hold.entries.values()]

    @classmethod
    def from_values(
        cls,
        *,
        release: str,
        values: Mapping[str, Iterable[str]],
    ) -> "Vocabulary":
        """Build a vocabulary from bare value lists (no labels).

        This is how an adapter hands over a template's two JSONB lists or a
        code-level enum without inventing metadata it does not have.
        """
        sets: dict[str, VocabularySet] = {}
        for name, raw in values.items():
            ordered = tuple(dict.fromkeys(str(v) for v in raw))
            sets[name] = VocabularySet(
                name=name,
                values=ordered,
                entries={
                    value: Entry(id=value, set_name=name, label_en=value, label_zh=value)
                    for value in ordered
                },
            )
        return cls(release=release, sets=sets)


def assert_additive(previous: Optional[Vocabulary], current: Vocabulary) -> None:
    """Fail when ``current`` removes an entry, a set, or changes an id.

    ``previous`` may be ``None`` (a first release has nothing to compare with).
    Ids are compared inside their set, so moving an entry between sets is also a
    removal — that is intentional: the URI embeds the set name, so a move *is* a
    new identity.
    """
    if previous is None:
        return

    removed_sets = sorted(set(previous.sets) - set(current.sets))
    if removed_sets:
        raise VocabEntryRemoved(
            f"vocabulary release {current.release!r} removed set(s) {removed_sets} "
            f"present in {previous.release!r}",
            sets=removed_sets,
            previous=previous.release,
            current=current.release,
        )

    missing: list[tuple[str, str]] = []
    for name, hold in previous.sets.items():
        now = current.sets[name]
        for value in hold.values:
            if not now.has(value):
                missing.append((name, value))

    if missing:
        detail = "; ".join(f"{name}/{value}" for name, value in missing)
        raise VocabEntryRemoved(
            f"vocabulary release {current.release!r} removed or renamed "
            f"{len(missing)} entr{'y' if len(missing) == 1 else 'ies'} present in "
            f"{previous.release!r}: {detail}. Retire with superseded_by instead.",
            entries=[{"set": name, "id": value} for name, value in missing],
            previous=previous.release,
            current=current.release,
        )
