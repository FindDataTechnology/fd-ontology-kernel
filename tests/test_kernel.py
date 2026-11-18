"""The kernel: one write path, folding at the boundary, publication by digest."""

from __future__ import annotations

import pytest

from fd_ontology_kernel import keys
from fd_ontology_kernel.errors import (
    InvariantViolated,
    PortMissing,
    ShapeDeclarationMissing,
    VocabOutOfSet,
)
from fd_ontology_kernel.kernel import AdapterDeclaration, Kernel, validate_declaration
from fd_ontology_kernel.keys import FoldRule
from fd_ontology_kernel.memory import (
    MemoryJournal,
    MemoryUnitStore,
    StaticVocabulary,
    memory_kernel,
    memory_vocabulary,
)


def test_write_creates_then_updates_with_a_paired_event() -> None:
    kernel = memory_kernel(manifest_shape=2)
    first = kernel.write(scope_id="d1", kind="law", external_key="合同法",
                         body="合同法", actor="u1")
    second = kernel.write(scope_id="d1", kind="law", external_key="合同法",
                          body="合同法（修订）", actor="u1")

    assert first.created is True and first.event.op == "create"
    assert second.created is False and second.event.op == "update"
    assert second.event.before["body"] == "合同法"
    assert second.event.after["body"] == "合同法（修订）"
    assert len(kernel.events.fold(scope_id="d1")) == 2


def test_write_without_an_actor_is_refused() -> None:
    kernel = memory_kernel()
    with pytest.raises(InvariantViolated):
        kernel.write(scope_id="d1", kind="law", external_key="合同法", body="b", actor="")


def test_out_of_vocabulary_kind_leaves_no_instance_and_no_event() -> None:
    kernel = memory_kernel()
    with pytest.raises(VocabOutOfSet):
        kernel.write(scope_id="d1", kind="penalty", external_key="罚款", body="b", actor="u")
    assert kernel.units.list_live(scope_id="d1") == []
    assert kernel.events.fold(scope_id="d1") == []


def test_key_is_folded_at_the_write_boundary_and_aliases_are_recorded() -> None:
    keys.register_rules("fold-domain", (FoldRule("drop-prc", "prefix", "中华人民共和国"),))
    kernel = memory_kernel(manifest_shape=2, fold_rules_for=keys.rules_for)
    written = kernel.write(scope_id="fold-domain", kind="law",
                           external_key="中华人民共和国合同法", body="合同法", actor="u")

    assert written.unit.external_key == "合同法"
    assert written.folded_from == "中华人民共和国合同法"
    assert written.unit.attrs["aliases"] == ["中华人民共和国合同法"]
    assert written.unit.attrs["folded_by"] == ["drop-prc"]


def test_folding_continues_to_work_when_the_second_write_uses_the_short_form() -> None:
    keys.register_rules("fold-domain-2", (FoldRule("drop-prc", "prefix", "中华人民共和国"),))
    kernel = memory_kernel(manifest_shape=2, fold_rules_for=keys.rules_for)
    kernel.write(scope_id="fold-domain-2", kind="law", external_key="中华人民共和国宪法",
                 body="宪法", actor="u")
    second = kernel.write(scope_id="fold-domain-2", kind="law", external_key="宪法",
                          body="宪法（修订）", actor="u")
    assert second.created is False
    assert kernel.units.raw_count() == 1


def test_a_domain_without_rules_folds_nothing() -> None:
    kernel = memory_kernel(manifest_shape=2, fold_rules_for=lambda scope: ())
    written = kernel.write(scope_id="d1", kind="law",
                           external_key="中华人民共和国合同法", body="b", actor="u")
    assert written.unit.external_key == "中华人民共和国合同法"
    assert written.folded_from is None


def test_a_bare_clause_number_is_refused() -> None:
    kernel = memory_kernel()
    with pytest.raises(InvariantViolated) as caught:
        kernel.write(scope_id="d1", kind="clause", external_key="第十二条", body="b", actor="u")
    assert "parent" in str(caught.value)


def test_a_clause_with_a_parent_is_accepted() -> None:
    kernel = memory_kernel()
    written = kernel.write(scope_id="d1", kind="clause",
                           external_key="合同法#第12条", body="第十二条", actor="u")
    assert written.created is True


def test_relationship_requires_both_endpoints_to_be_live() -> None:
    kernel = memory_kernel()
    with pytest.raises(InvariantViolated) as caught:
        kernel.write_relationship(scope_id="d1", kind="belongs_to",
                                  from_key="订单", to_key="客户", actor="u")
    assert sorted(caught.value.context["missing"]) == ["客户", "订单"]

    kernel.write(scope_id="d1", kind="entity", external_key="订单", body="订单", actor="u")
    kernel.write(scope_id="d1", kind="entity", external_key="客户", body="客户", actor="u")
    written = kernel.write_relationship(scope_id="d1", kind="belongs_to",
                                        from_key="订单", to_key="客户", actor="u")
    assert written.unit.external_key == "订单->belongs_to:客户"
    assert written.unit.attrs["from_key"] == "订单"


def test_publish_is_idempotent_by_digest() -> None:
    kernel = memory_kernel(manifest_shape=2)
    kernel.write(scope_id="d1", kind="law", external_key="合同法", body="合同法", actor="u")

    first, unchanged_first = kernel.publish(scope_id="d1", published_by="lead")
    second, unchanged_second = kernel.publish(scope_id="d1", published_by="lead")

    assert unchanged_first is False and first.version == 1
    assert unchanged_second is True and second.version == 1
    assert kernel.publications.count() == 1


def test_publish_after_a_change_mints_the_next_version_and_keeps_the_old_one() -> None:
    kernel = memory_kernel(manifest_shape=2)
    kernel.write(scope_id="d1", kind="law", external_key="合同法", body="v1", actor="u")
    first, _ = kernel.publish(scope_id="d1", published_by="lead")
    kernel.write(scope_id="d1", kind="law", external_key="合同法", body="v2", actor="u")
    second, unchanged = kernel.publish(scope_id="d1", published_by="lead")

    assert second.version == 2 and unchanged is False
    assert second.digest != first.digest
    assert kernel.publications.get(scope_id="d1", version=1).payload["entities"][0]["body"] == "v1"
    assert kernel.publications.get(scope_id="d1", version=2).payload["entities"][0]["body"] == "v2"


def test_publishing_nothing_is_refused() -> None:
    kernel = memory_kernel(manifest_shape=2)
    with pytest.raises(InvariantViolated):
        kernel.publish(scope_id="d1", published_by="lead")


def test_publish_needs_the_publications_port() -> None:
    kernel = Kernel(units=MemoryUnitStore(), events=MemoryJournal(),
                    vocabulary=StaticVocabulary(memory_vocabulary()))
    with pytest.raises(PortMissing):
        kernel.publish(scope_id="d1", published_by="lead")


def test_declaration_is_required_and_validated() -> None:
    with pytest.raises(ShapeDeclarationMissing):
        validate_declaration(None)
    with pytest.raises(ShapeDeclarationMissing):
        validate_declaration(AdapterDeclaration(system="x", kernel_contract="2.0", manifest_shape=1))
    with pytest.raises(ShapeDeclarationMissing):
        validate_declaration(AdapterDeclaration(system="x", kernel_contract="1.0", manifest_shape=9))

    accepted = validate_declaration(
        AdapterDeclaration(system="x", kernel_contract="1.0", manifest_shape=1)
    )
    assert accepted.system == "x"


def test_vocabulary_source_is_asked_per_write() -> None:
    class Counting(StaticVocabulary):
        def load(self, *, scope_id: str):
            self.calls += 1
            return super().load(scope_id=scope_id)

    vocabulary = Counting(memory_vocabulary())
    kernel = Kernel(units=MemoryUnitStore(), events=MemoryJournal(), vocabulary=vocabulary)
    kernel.write(scope_id="d1", kind="law", external_key="合同法", body="b", actor="u")
    kernel.write(scope_id="d1", kind="law", external_key="劳动法", body="b", actor="u")
    assert vocabulary.calls == 2


def test_a_failing_event_append_propagates_and_leaves_the_pair_incomplete() -> None:
    """The kernel does not swallow it: an adapter must roll the upsert back.

    The unit store here is a plain dict, so the upsert is still visible after the
    failure — which is exactly the window the adapter's transaction exists to
    close. Asserting it makes the requirement explicit instead of implicit.
    """
    from fd_ontology_kernel.memory import MemoryUnitStore

    class BrokenJournal(MemoryJournal):
        def append(self, event):
            raise RuntimeError("journal unavailable")

    units = MemoryUnitStore()
    kernel = Kernel(units=units, events=BrokenJournal(),
                    vocabulary=StaticVocabulary(memory_vocabulary()))
    with pytest.raises(RuntimeError):
        kernel.write(scope_id="d1", kind="law", external_key="合同法", body="b", actor="u")
    assert len(kernel.events.fold(scope_id="d1")) == 0
    assert units.get(scope_id="d1", kind="law", external_key="合同法") is not None


def test_remove_soft_deletes_and_journals_a_delete_event() -> None:
    kernel = memory_kernel(manifest_shape=2)
    kernel.write(scope_id="d1", kind="law", external_key="合同法", body="合同法", actor="u")
    removed = kernel.remove(scope_id="d1", kind="law", external_key="合同法", actor="u")

    assert removed.event.op == "delete"
    assert removed.event.before["body"] == "合同法"
    assert removed.event.after is None
    assert removed.unit.tags["deleted"] is True
    assert removed.unit.body == "合同法"                     # content preserved
    assert kernel.units.list_live(scope_id="d1") == []       # no longer live


def test_remove_of_a_missing_unit_is_refused() -> None:
    kernel = memory_kernel(manifest_shape=2)
    with pytest.raises(InvariantViolated):
        kernel.remove(scope_id="d1", kind="law", external_key="不存在", actor="u")


def test_remove_refuses_a_kind_outside_the_vocabulary() -> None:
    kernel = memory_kernel(manifest_shape=2)
    with pytest.raises(VocabOutOfSet):
        kernel.remove(scope_id="d1", kind="penalty", external_key="罚款", actor="u")
