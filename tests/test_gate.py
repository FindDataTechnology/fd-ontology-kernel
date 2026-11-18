"""The gate as CI calls it: exit codes, line shape, and no false green."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fd_ontology_kernel.conformance.gate import main
from fd_ontology_kernel.conformance.suite import assertion_ids


def test_gate_passes_on_the_reference_adapter(capsys) -> None:
    code = main(["--adapter", "memory"])
    out = capsys.readouterr().out
    assert code == 0
    assert "PASS:" in out
    assert str(len(assertion_ids())) in out


def test_gate_lists_assertions_and_adapters(capsys) -> None:
    code = main(["--list"])
    out = capsys.readouterr().out
    assert code == 0
    for assertion_id in ("scope-isolation", "write-event-pair", "pull-trail"):
        assert assertion_id in out
    assert "memory" in out


def test_gate_without_a_selection_is_a_usage_error(capsys) -> None:
    assert main([]) == 2
    assert "nothing selected" in capsys.readouterr().err


def test_gate_fails_a_broken_adapter_with_a_named_violation_and_a_fix(tmp_path: Path, capsys) -> None:
    registry = tmp_path / "adapters.json"
    registry.write_text(json.dumps({
        "kernel_contract": "1.0",
        "adapters": [{
            "name": "broken",
            "system": "conformance-broken-fixture",
            "module": "tests.fixtures.broken_adapter",
            "factory": "build",
            "kernel_contract": "1.0",
            "manifest_shape": 2,
        }],
    }), encoding="utf-8")

    code = main(["--adapter", "broken", "--registry", str(registry)])
    out = capsys.readouterr().out
    assert code == 1
    assert "write-event-pair" in out
    assert "修法：" in out
    assert "FAILED:" in out


def test_gate_reports_an_unreadable_registry_as_exit_2(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "adapters.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main(["--adapter", "memory", "--registry", str(bad)]) == 2
    assert "registry unreadable" in capsys.readouterr().err


def test_gate_reports_an_unregistered_adapter_by_name(tmp_path: Path, capsys) -> None:
    registry = tmp_path / "adapters.json"
    registry.write_text(json.dumps({"kernel_contract": "1.0", "adapters": []}),
                        encoding="utf-8")
    code = main(["--adapter", "ghost", "--registry", str(registry)])
    out = capsys.readouterr().out
    assert code == 1
    assert "no registry entry named 'ghost'" in out


def test_gate_scan_flags_an_unregistered_implementation(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "unregistered-system"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "adapter.py").write_text(
        "import fd_ontology_kernel\n", encoding="utf-8"
    )
    code = main(["--adapter", "memory", "--scan", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "registry:unregistered" in out


def test_gate_self_check_runs_by_default_and_can_be_skipped(capsys) -> None:
    assert main(["--adapter", "memory"]) == 0
    capsys.readouterr()
    assert main(["--adapter", "memory", "--skip-self-check"]) == 0
    assert "PASS:" in capsys.readouterr().out
