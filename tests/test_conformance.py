"""Conformance behaviour: every assertion passes on the reference adapter, and
every assertion fails on its own counterexample.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fd_ontology_kernel.conformance import assertions as assertions_mod
from fd_ontology_kernel.conformance.memory_adapter import build
from fd_ontology_kernel.conformance.spec import (
    AdapterSpec,
    Violation,
    capabilities_problems,
    load_adapter,
    read_registry,
    scan_unregistered,
)
from fd_ontology_kernel.conformance.suite import (
    ASSERTIONS,
    assertion_ids,
    run_all,
    run_all_with_skips,
    run_assertion,
    self_check,
    validate_registry_of_assertions,
)
from fd_ontology_kernel.kernel import AdapterDeclaration, Kernel
from fd_ontology_kernel.memory import (
    MemoryJournal,
    MemoryProposalBox,
    MemoryPublicationStore,
    MemoryRegistry,
    MemoryUnitStore,
    StaticVocabulary,
    memory_vocabulary,
)


def test_every_assertion_is_registered_with_an_invariant_and_a_fix() -> None:
    assert validate_registry_of_assertions() == []
    assert len(ASSERTIONS) >= 15
    assert len(assertion_ids()) == len(set(assertion_ids()))


def test_the_reference_adapter_passes_every_assertion() -> None:
    violations = run_all(build())
    assert violations == [], "\n".join(v.render() for v in violations)


def test_every_assertion_fails_on_its_counterexample() -> None:
    """The suite's own red test: an assertion that cannot fail is not admitted."""
    violations = self_check()
    assert violations == [], "\n".join(v.render() for v in violations)


def test_an_exploding_assertion_is_a_violation_not_a_crash() -> None:
    def explode(spec: AdapterSpec) -> list[str]:
        raise RuntimeError("boom")

    assertion = assertions_mod.Assertion(
        id="explodes", invariant="x", description="x", fix="fix it",
        run=explode, counterexample=lambda: True,
    )
    violations = run_assertion(build(), assertion)
    assert len(violations) == 1
    assert "RuntimeError" in violations[0].fact


def test_run_all_can_select_a_subset_and_names_a_missing_one() -> None:
    violations = run_all(build(), only=["scope-isolation"])
    assert violations == []
    missing = run_all(build(), only=["no-such-assertion"])
    assert missing and "no assertion named" in missing[0].fact


def test_violation_renders_system_assertion_fact_and_fix() -> None:
    line = Violation(system="sys", assertion_id="a", fact="f", fix="改这里").render()
    assert line == "sys · a · f · 修法：改这里"


def test_registry_lists_the_reference_adapter() -> None:
    entries = read_registry()
    names = [entry.name for entry in entries]
    assert names[0] == "memory"
    assert "fd-open-data-protocol" in names
    memory = entries[0]
    assert memory.manifest_shape == 2


def test_a_registered_surface_declares_its_import_paths() -> None:
    """A sibling checkout is named in the registry, not silently skipped."""
    protocol = [entry for entry in read_registry() if entry.name == "fd-open-data-protocol"][0]
    assert protocol.paths, "a non-installed surface must declare paths"
    assert any("fd-open-data-protocol" in path for path in protocol.paths)


def test_an_adapter_without_clause_kinds_reports_a_skip_not_a_pass() -> None:
    """'Not applicable' and 'verified' are different facts and must look different."""
    kernel_spec = build()
    base = kernel_spec.sandbox()
    # Same sandbox, but clause support switched off, as a vocabulary-only surface
    # has: the clause rule is vacuous there.
    spec = AdapterSpec(
        name="no-clauses",
        system="conformance-no-clauses",
        declaration=AdapterDeclaration(
            system="conformance-no-clauses", kernel_contract="1.0", manifest_shape=2
        ),
        sandbox=lambda: _without_clause_kinds(base),
    )
    violations, skips = run_all_with_skips(spec)
    skipped_ids = {assertion_id for assertion_id, _reason in skips}
    assert "clause-parent" in skipped_ids
    assert all(v.assertion_id != "clause-parent" for v in violations), (
        "a skipped assertion must not also be reported as a pass or a failure"
    )
    assert len(skips) == 1  # only the clause rule is inapplicable


def test_declared_capabilities_turn_exactly_their_rules_into_skips() -> None:
    """A surface with no writable proposals and no registry says so; those rules skip.

    The point is that the adapter has to *declare* it: leaving ``capabilities``
    unset is checked in full, so silence can never buy a skip.
    """
    base = build()
    spec = AdapterSpec(
        name="projection-only",
        system="conformance-projection-only",
        declaration=AdapterDeclaration(
            system="conformance-projection-only", kernel_contract="1.0", manifest_shape=2
        ),
        sandbox=base.sandbox,
        capabilities={"proposals": "projection", "distribution": "absent"},
    )
    violations, skips = run_all_with_skips(spec)
    skipped_ids = {assertion_id for assertion_id, _reason in skips}
    assert skipped_ids == {
        "nomination-not-ontology",
        "claim-identity",
        "stale-evidence",
        "pull-trail",
    }
    assert all(v.assertion_id not in skipped_ids for v in violations)

    reasons = dict(skips)
    assert "proposals='projection'" in reasons["claim-identity"]
    assert "distribution='absent'" in reasons["pull-trail"]


def test_an_unset_capability_map_is_checked_in_full() -> None:
    """No declaration = assume everything: the same sandbox now runs those rules."""
    base = build()
    spec = AdapterSpec(
        name="undeclared",
        system="conformance-undeclared",
        declaration=AdapterDeclaration(
            system="conformance-undeclared", kernel_contract="1.0", manifest_shape=2
        ),
        sandbox=base.sandbox,
    )
    _violations, skips = run_all_with_skips(spec)
    skipped_ids = {assertion_id for assertion_id, _reason in skips}
    assert "claim-identity" not in skipped_ids
    assert "pull-trail" not in skipped_ids


def test_a_misspelled_capability_is_a_violation() -> None:
    """A typo must not behave like 'nothing declared' — that would silently widen skips."""
    assert capabilities_problems({"proposal": "absent"})
    assert capabilities_problems({"proposals": "read-only"})
    assert capabilities_problems({"proposals": "projection"}) == []
    assert capabilities_problems(None) == []


def test_the_worked_example_passes_the_whole_suite() -> None:
    """``examples/toy_adapter.py`` is the README's claim; a rotted example is worse than none."""
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "examples" / "toy_adapter.py"
    module_spec = importlib.util.spec_from_file_location("toy_adapter", path)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)

    violations, skips = run_all_with_skips(module.build())
    assert violations == [], "\n".join(v.render() for v in violations)
    assert skips == [], "the example claims every capability, so nothing may be skipped"


def _without_clause_kinds(template):
    """A fresh kernel with the same ports but no clause kinds declared."""
    return Kernel(
        units=MemoryUnitStore(),
        events=MemoryJournal(),
        vocabulary=StaticVocabulary(memory_vocabulary()),
        proposals=MemoryProposalBox(),
        publications=MemoryPublicationStore(),
        distribution=MemoryRegistry(),
        relationship_kinds=("belongs_to", "cites_law", "defines"),
        clause_kinds=(),
        manifest_shape=2,
    )


def test_registry_entry_loads_into_a_spec() -> None:
    entry = read_registry()[0]
    spec, violations = load_adapter(entry)
    assert violations == []
    assert spec is not None and spec.system == "fd-ontology-kernel"


def test_registry_reports_an_unimportable_module() -> None:
    entry = read_registry()[0]
    broken = entry.__class__(
        name=entry.name, system=entry.system, module="does.not.exist",
        factory=entry.factory, kernel_contract=entry.kernel_contract,
        manifest_shape=entry.manifest_shape,
    )
    spec, violations = load_adapter(broken)
    assert spec is None
    assert violations and violations[0].assertion_id == "registry:importable"


def test_registry_reports_a_missing_factory(tmp_path: Path) -> None:
    entry = read_registry()[0]
    missing = entry.__class__(
        name=entry.name, system=entry.system, module=entry.module,
        factory="no_such_factory", kernel_contract=entry.kernel_contract,
        manifest_shape=entry.manifest_shape,
    )
    spec, violations = load_adapter(missing)
    assert spec is None
    assert violations[0].assertion_id == "registry:entrypoint"


def test_registry_reports_declaration_drift() -> None:
    entry = read_registry()[0]
    drifted = entry.__class__(
        name=entry.name, system=entry.system, module=entry.module,
        factory=entry.factory, kernel_contract="9.0", manifest_shape=1,
    )
    spec, violations = load_adapter(drifted)
    assert spec is not None
    ids = {violation.assertion_id for violation in violations}
    assert "registry:drift" in ids
    assert "registry:declaration" in ids


def test_scan_flags_an_unregistered_implementation(tmp_path: Path) -> None:
    repo = tmp_path / "some-system"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "adapter.py").write_text(
        "from fd_ontology_kernel.kernel import Kernel\n", encoding="utf-8"
    )
    violations = scan_unregistered([tmp_path])
    assert violations
    assert violations[0].assertion_id == "registry:unregistered"
    assert "some-system" in violations[0].system


def test_scan_ignores_a_registered_system(tmp_path: Path) -> None:
    repo = tmp_path / "fd-ontology-kernel"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "adapter.py").write_text(
        "from fd_ontology_kernel.kernel import Kernel\n", encoding="utf-8"
    )
    assert scan_unregistered([tmp_path]) == []
