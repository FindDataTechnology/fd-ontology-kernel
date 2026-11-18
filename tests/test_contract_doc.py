"""CONTRACT.md is load-bearing: every error code must appear in it."""

from __future__ import annotations

from pathlib import Path

from fd_ontology_kernel import errors

CONTRACT = Path(__file__).resolve().parents[1] / "CONTRACT.md"


def test_contract_document_exists() -> None:
    assert CONTRACT.is_file(), f"missing {CONTRACT}"


def test_every_error_code_is_documented() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    missing = [code.value for code in errors.ErrorCode if code.value not in text]
    assert missing == [], f"CONTRACT.md does not document: {missing}"


def test_contract_documents_every_port() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    for name in ("UnitStore", "EventJournal", "ProposalBox", "PublicationStore",
                 "VocabularySource", "DistributionRegistry"):
        assert name in text, f"CONTRACT.md does not describe the {name} port"


def test_contract_states_the_shape_versions_it_supports() -> None:
    from fd_ontology_kernel.manifest import SUPPORTED_SHAPES

    text = CONTRACT.read_text(encoding="utf-8")
    for version in SUPPORTED_SHAPES:
        assert f"| {version} |" in text, f"CONTRACT.md omits shape {version}"


def test_contract_version_matches_the_module() -> None:
    from fd_ontology_kernel.kernel import KERNEL_CONTRACT_VERSION

    text = CONTRACT.read_text(encoding="utf-8")
    assert KERNEL_CONTRACT_VERSION in text
