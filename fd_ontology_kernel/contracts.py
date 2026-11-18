"""Data shapes the kernel passes around.

Everything here is a frozen dataclass: an event that can be mutated after it was
appended is not an append-only event, and a manifest that can be edited after it
was digested cannot be verified. Freezing is the cheapest way to make that
impossible rather than merely discouraged.

The one shape that deserves explanation is :class:`ManifestEnvelope`: the shape
version lives *outside* the payload, never inside it. Putting it inside would
change the payload and therefore every digest ever published — the one mistake
that would invalidate all existing versions at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

#: Operations an event may record.
EVENT_OPS: tuple[str, ...] = ("create", "update", "delete")
#: Proposal states. ``pending`` is the only one a machine can produce.
PROPOSAL_STATES: tuple[str, ...] = ("pending", "confirmed", "rejected")
#: Knowledge tiers, shared by both instance surfaces.
TIERS: tuple[str, ...] = ("raw", "bronze", "silver", "gold")


@dataclass(frozen=True)
class Scope:
    """One isolation boundary (a domain, a customer, a tenant).

    ``kind`` is descriptive only — the kernel never branches on it, it exists so
    logs and ledgers can say "customer" or "domain" instead of "scope".
    """

    id: str
    kind: str = "scope"

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("a scope needs an id")


@dataclass(frozen=True)
class Unit:
    """One ontology instance: an entity or a relationship, addressed by key."""

    scope_id: str
    kind: str
    external_key: str
    body: str = ""
    tier: str = "raw"
    tags: Mapping[str, Any] = field(default_factory=dict)
    attrs: Mapping[str, Any] = field(default_factory=dict)
    source_path: Optional[str] = None
    provenance: Mapping[str, Any] = field(default_factory=dict)
    #: The adapter's own row image, when it has one. The kernel never persists or
    #: interprets it; it exists so an adapter whose event payload is a published
    #: contract (a full row with its surrogate id and timestamps) can journal that
    #: shape instead of the kernel's portable projection.
    raw: Optional[Mapping[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.scope_id:
            raise ValueError("a unit needs a scope_id")
        if not self.kind:
            raise ValueError("a unit needs a kind")
        if not self.external_key:
            raise ValueError("a unit needs an external_key")

    def with_key(self, external_key: str, **attrs: Any) -> "Unit":
        """Return a copy under another key (used by write-boundary folding)."""
        merged = dict(self.attrs)
        for name, value in attrs.items():
            if value is not None:
                merged[name] = value
        return Unit(
            scope_id=self.scope_id,
            kind=self.kind,
            external_key=external_key,
            body=self.body,
            tier=self.tier,
            tags=dict(self.tags),
            attrs=merged,
            source_path=self.source_path,
            provenance=dict(self.provenance),
            raw=self.raw,
        )


def relationship_key(*, from_key: str, kind: str, to_key: str) -> str:
    """The deterministic external key of a relationship unit.

    Distribution consumers rebuild relationship identity from this string
    alone, so its shape is a published contract — do not change it.
    """
    return f"{from_key}->{kind}:{to_key}"


@dataclass(frozen=True)
class Relationship:
    """A relationship expressed as its two endpoints.

    Relationships are stored as ordinary units whose ``attrs`` carry the
    endpoints; this shape is how callers talk about them.
    """

    scope_id: str
    kind: str
    from_key: str
    to_key: str
    body: str = ""
    tier: str = "raw"
    tags: Mapping[str, Any] = field(default_factory=dict)
    attrs: Mapping[str, Any] = field(default_factory=dict)

    @property
    def external_key(self) -> str:
        return relationship_key(from_key=self.from_key, kind=self.kind, to_key=self.to_key)

    def to_unit(self) -> Unit:
        attrs = dict(self.attrs)
        attrs["from_key"] = self.from_key
        attrs["to_key"] = self.to_key
        body = self.body or f"{self.from_key} {self.kind} {self.to_key}"
        return Unit(
            scope_id=self.scope_id,
            kind=self.kind,
            external_key=self.external_key,
            body=body,
            tier=self.tier,
            tags=dict(self.tags),
            attrs=attrs,
        )


@dataclass(frozen=True)
class Evidence:
    """What a nomination rests on: a source identifier and a verbatim quote."""

    source: str
    quote: str
    locator: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("evidence needs a source")
        if not self.quote:
            raise ValueError("evidence needs a quote")


@dataclass(frozen=True)
class Event:
    """One append-only record of a content change.

    ``target_key`` is the portable identity (required). ``target_ref`` is the
    adapter's own surrogate id where it has one — kept optional so a surface
    that addresses by key (a domain) and one that addresses by id (a customer)
    can both journal without rewriting history.
    """

    scope_id: str
    op: str
    target_kind: str
    target_key: str
    actor: str
    via: str
    before: Optional[Mapping[str, Any]] = None
    after: Optional[Mapping[str, Any]] = None
    target_ref: Optional[Any] = None
    seq: Optional[int] = None
    source_ref: Optional[str] = None
    source_instance_id: Optional[int] = None
    occurred_at: Optional[str] = None

    def __post_init__(self) -> None:
        if self.op not in EVENT_OPS:
            raise ValueError(f"op must be one of {EVENT_OPS}, got {self.op!r}")
        if not self.target_key:
            raise ValueError("an event needs its target_key")
        if self.op == "create" and self.before is not None:
            raise ValueError("a create event cannot carry a before state")
        if self.op == "delete" and self.after is not None:
            raise ValueError("a delete event cannot carry an after state")


@dataclass(frozen=True)
class Proposal:
    """A nomination awaiting a human decision."""

    scope_id: str
    source: str
    kind: str
    external_key: str
    body: str
    evidence: Mapping[str, Any]
    evidence_digest: str
    submitted_by: str
    state: str = "pending"
    sources: Sequence[str] = ()
    impact: str = ""
    proposal_id: Optional[int] = None
    decided_by: Optional[str] = None
    decision_note: Optional[str] = None

    def __post_init__(self) -> None:
        if self.state not in PROPOSAL_STATES:
            raise ValueError(f"state must be one of {PROPOSAL_STATES}, got {self.state!r}")
        if not self.external_key:
            raise ValueError("a proposal needs an external_key")
        if not tuple(self.sources):
            object.__setattr__(self, "sources", (self.source,))

    @property
    def submitters(self) -> tuple[str, ...]:
        """Every party that has submitted this claim (first submitter first)."""
        seen: list[str] = []
        for name in (self.source,) + tuple(self.sources):
            if name and name not in seen:
                seen.append(name)
        return tuple(seen)


@dataclass(frozen=True)
class Publication:
    """An immutable snapshot: a content digest plus the payload it anchors."""

    scope_id: str
    version: int
    digest: str
    payload: Mapping[str, Any]
    manifest_schema_version: int = 1
    trigger: str = "manual"
    label: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    #: Adapter-specific columns the kernel does not interpret (e.g. a changelog).
    #: Kept out of the payload so it never enters the digest.
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("a publication version starts at 1")
        if not self.digest:
            raise ValueError("a publication needs a digest")


@dataclass(frozen=True)
class ManifestEnvelope:
    """A payload plus the shape version needed to interpret it.

    The version is deliberately not part of ``payload``: the digest is computed
    over ``payload`` only, so adding the version inside would change every
    already-published digest.
    """

    manifest_schema_version: int
    payload: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest_schema_version": self.manifest_schema_version,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class Consumer:
    """A registered distribution consumer (credential stored as a digest)."""

    consumer_id: int
    name: str
    scopes: Sequence[str]
    token_hash: str
    created_by: str
    revoked_at: Optional[str] = None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None


@dataclass(frozen=True)
class PullEvent:
    """One successful pull of a published version by a consumer."""

    consumer_id: int
    scope_id: str
    version: int
    happened_at: Optional[str] = None
