"""Distribution: credentials by digest, and a trail that only records successes.

Two rules, both stated in the spec because both are easy to get wrong:

* **Credentials are stored as digests.** The plaintext token never touches the
  registry, so a database read cannot leak a working credential.
* **Only successful pulls are recorded.** A rejected credential or a request for
  a version that does not exist leaves no row. A trail that also records refusals
  would let a caller claim "we pulled v7" when nothing was ever served, which is
  exactly the kind of quiet lie a ledger exists to prevent.

The pull is composed here rather than in the adapter so the ordering cannot
drift: authenticate → resolve → record.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from .contracts import Consumer, Publication, PullEvent
from .errors import InvariantViolated
from .ports import DistributionRegistry, PublicationStore

#: Prefix on stored digests, so a reader can tell a digest from a raw token.
DIGEST_PREFIX = "sha256:"


def token_hash(token: str) -> str:
    """Digest of a consumer credential. Empty tokens are refused, not hashed."""
    value = (token or "").strip()
    if not value:
        raise InvariantViolated("a consumer credential cannot be empty")
    return DIGEST_PREFIX + hashlib.sha256(value.encode("utf-8")).hexdigest()


def authenticate(
    registry: DistributionRegistry, *, token: str, scope_id: str
) -> Consumer:
    """Resolve a credential to a live consumer with access to ``scope_id``."""
    consumer = registry.find_by_token_hash(token_hash=token_hash(token))
    if consumer is None:
        raise InvariantViolated(
            "unknown consumer credential", scope_id=scope_id
        )
    if consumer.revoked:
        raise InvariantViolated(
            f"consumer {consumer.name!r} is revoked", consumer=consumer.name
        )
    if scope_id not in tuple(consumer.scopes):
        raise InvariantViolated(
            f"consumer {consumer.name!r} has no access to scope {scope_id!r}",
            consumer=consumer.name,
            scopes=list(consumer.scopes),
        )
    return consumer


def pull(
    *,
    registry: DistributionRegistry,
    store: PublicationStore,
    token: str,
    scope_id: str,
    version: Optional[int] = None,
) -> tuple[Publication, Consumer, PullEvent]:
    """Serve one published version and record it — or fail without recording."""
    consumer = authenticate(registry, token=token, scope_id=scope_id)
    publication = (
        store.get(scope_id=scope_id, version=version)
        if version is not None
        else store.latest(scope_id=scope_id)
    )
    if publication is None:
        raise InvariantViolated(
            "no such published version"
            if version is not None
            else f"scope {scope_id!r} has never been published",
            scope_id=scope_id,
            version=version,
        )
    event = registry.record_pull(
        consumer_id=consumer.consumer_id,
        scope_id=scope_id,
        version=publication.version,
    )
    return publication, consumer, event


def last_pull_by_consumer(
    registry: DistributionRegistry,
) -> dict[int, PullEvent]:
    """The most recent pull per consumer (consumers with none are absent)."""
    latest: dict[int, PullEvent] = {}
    for event in registry.pulls_since():
        current = latest.get(event.consumer_id)
        if current is None or (event.happened_at or "") >= (current.happened_at or ""):
            latest[event.consumer_id] = event
    return latest


def never_pulled(registry: DistributionRegistry) -> list[Consumer]:
    """Registered consumers that have never completed a pull."""
    pulled = set(last_pull_by_consumer(registry))
    return [
        consumer for consumer in registry.consumers() if consumer.consumer_id not in pulled
    ]
