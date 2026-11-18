"""Ports: the in-memory adapter implements all six, and a missing one is named."""

from __future__ import annotations

import pytest

from fd_ontology_kernel.errors import PortMissing
from fd_ontology_kernel.kernel import Kernel
from fd_ontology_kernel.memory import (
    MemoryJournal,
    MemoryProposalBox,
    MemoryPublicationStore,
    MemoryRegistry,
    MemoryUnitStore,
    StaticVocabulary,
    memory_vocabulary,
)
from fd_ontology_kernel.ports import (
    PORTS,
    DistributionRegistry,
    EventJournal,
    ProposalBox,
    PublicationStore,
    UnitStore,
    VocabularySource,
)


def test_memory_adapter_satisfies_every_port() -> None:
    vocabulary = StaticVocabulary(memory_vocabulary())
    instances = {
        "units": MemoryUnitStore(),
        "events": MemoryJournal(),
        "proposals": MemoryProposalBox(),
        "publications": MemoryPublicationStore(),
        "vocabulary": vocabulary,
        "distribution": MemoryRegistry(),
    }
    assert set(instances) == set(PORTS)
    for name, instance in instances.items():
        assert isinstance(instance, PORTS[name]), f"{name} does not satisfy {PORTS[name]}"


def test_protocols_are_runtime_checkable() -> None:
    for protocol in (UnitStore, EventJournal, ProposalBox, PublicationStore,
                     VocabularySource, DistributionRegistry):
        assert getattr(protocol, "_is_runtime_protocol", False) is True


def test_kernel_names_the_missing_port() -> None:
    with pytest.raises(PortMissing) as caught:
        Kernel(units=MemoryUnitStore(), events=None, vocabulary=StaticVocabulary(memory_vocabulary()))  # type: ignore[arg-type]
    assert caught.value.context["port"] == "events"


def test_journal_has_no_mutation_surface() -> None:
    """The append-only claim is structural: there is nothing to call."""
    journal = MemoryJournal()
    for forbidden in ("update", "delete", "rewrite", "remove"):
        assert not hasattr(journal, forbidden)
