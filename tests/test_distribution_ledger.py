"""Distribution and the ledger: success-only trail, gap visibility, no writes."""

from __future__ import annotations

import pytest

from fd_ontology_kernel import distribution, ledger
from fd_ontology_kernel.contracts import Unit
from fd_ontology_kernel.errors import InvariantViolated
from fd_ontology_kernel.memory import (
    MemoryPublicationStore,
    MemoryRegistry,
    MemoryUnitStore,
    memory_kernel,
    memory_vocabulary,
)


def test_token_hash_is_a_digest_not_the_token() -> None:
    digest = distribution.token_hash("secret-token")
    assert digest.startswith("sha256:")
    assert "secret-token" not in digest
    assert digest == distribution.token_hash("secret-token")
    assert digest != distribution.token_hash("other-token")


def test_empty_token_is_refused() -> None:
    with pytest.raises(InvariantViolated):
        distribution.token_hash("   ")


def test_pull_records_only_on_success() -> None:
    kernel = memory_kernel(manifest_shape=2)
    registry = kernel.distribution
    consumer = registry.register(
        name="reader", scopes=("d1",), token_hash=distribution.token_hash("good"), created_by="lead"
    )
    kernel.write(scope_id="d1", kind="law", external_key="合同法", body="b", actor="u")
    publication, _ = kernel.publish(scope_id="d1", published_by="lead")

    with pytest.raises(InvariantViolated):
        distribution.pull(registry=registry, store=kernel.publications,
                          token="bad", scope_id="d1")
    assert registry.pulls_since() == []

    with pytest.raises(InvariantViolated):
        distribution.pull(registry=registry, store=kernel.publications,
                          token="good", scope_id="d1", version=99)
    assert registry.pulls_since() == []

    served, served_by, event = distribution.pull(
        registry=registry, store=kernel.publications, token="good", scope_id="d1"
    )
    assert served.version == publication.version
    assert served_by.consumer_id == consumer.consumer_id
    assert event.version == publication.version
    assert len(registry.pulls_since()) == 1


def test_scope_access_and_revocation_are_enforced() -> None:
    registry = MemoryRegistry()
    registry.register(name="reader", scopes=("d1",),
                      token_hash=distribution.token_hash("t"), created_by="lead")
    with pytest.raises(InvariantViolated):
        distribution.authenticate(registry, token="t", scope_id="d2")

    consumer = registry.find_by_token_hash(token_hash=distribution.token_hash("t"))
    registry.revoke(consumer_id=consumer.consumer_id)
    with pytest.raises(InvariantViolated) as caught:
        distribution.authenticate(registry, token="t", scope_id="d1")
    assert "revoked" in str(caught.value)


def test_ledger_separates_gaps_from_smoke() -> None:
    vocabulary = memory_vocabulary(
        entity_types=("law", "clause", "concept"), relationship_kinds=("cites_law",)
    )
    units = [
        Unit(scope_id="d1", kind="law", external_key="合同法", body="合同法"),
        Unit(scope_id="d1", kind="entity", external_key="smoke-entity", body="冒烟"),
    ]
    report = ledger.project(vocabulary=vocabulary, units=units)

    assert report.gaps["entity_types"] == ["clause", "concept"]
    assert report.gaps["relationship_kinds"] == ["cites_law"]
    assert [row["external_key"] for row in report.smoke] == ["smoke-entity"]
    assert report.curation["total"] == 2
    assert "entity" in report.reconcile["out_of_vocabulary"]


def test_ledger_reports_never_pulled_consumers_and_publication_state() -> None:
    kernel = memory_kernel(manifest_shape=2)
    kernel.write(scope_id="d1", kind="law", external_key="合同法", body="b", actor="u")
    publication, _ = kernel.publish(scope_id="d1", published_by="lead")

    registry = kernel.distribution
    registry.register(name="never", scopes=("d1",),
                      token_hash=distribution.token_hash("n"), created_by="lead")
    pulled = registry.register(name="reader", scopes=("d1",),
                               token_hash=distribution.token_hash("r"), created_by="lead")
    registry.record_pull(consumer_id=pulled.consumer_id, scope_id="d1", version=1)

    report = ledger.project(
        vocabulary=memory_vocabulary(),
        units=kernel.units.list_live(scope_id="d1"),
        publication=publication,
        consumers=registry.consumers(),
        pulls=registry.pulls_since(),
        relationship_kinds=("belongs_to", "cites_law", "defines"),
        unpublished_changes=0,
    )

    assert report.publication["latest_version"] == 1
    assert report.publication["manifest_schema_version"] == 2
    assert report.consumption["never_pulled"] == ["never"]
    assert {row["name"]: row["pulls"] for row in report.consumption["consumers"]} == {
        "never": 0,
        "reader": 1,
    }


def test_ledger_projection_calls_no_write_port() -> None:
    class WriteHostileStore(MemoryUnitStore):
        def upsert(self, unit):  # pragma: no cover - must never run
            raise AssertionError("the ledger must not write")

    store = WriteHostileStore()
    store.upsert_runs = 0
    store._rows[("d1", "law", "合同法")] = Unit(
        scope_id="d1", kind="law", external_key="合同法", body="b"
    )
    report = ledger.project(vocabulary=memory_vocabulary(), units=store.list_live(scope_id="d1"))
    assert report.curation["total"] == 1


def test_unpublished_change_count_is_reported_verbatim() -> None:
    report = ledger.project(
        vocabulary=memory_vocabulary(),
        units=[Unit(scope_id="d1", kind="law", external_key="合同法", body="b")],
        unpublished_changes=3,
    )
    assert report.reconcile["unpublished_changes"] == 3
