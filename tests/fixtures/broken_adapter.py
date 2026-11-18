"""A deliberately broken adapter: the gate's negative control.

It wires a journal that accepts events and keeps none, which is the single most
common way a "working" adapter undermines the trail: writes succeed, the audit
is empty. The gate must refuse it.
"""

from __future__ import annotations

from fd_ontology_kernel.conformance.spec import AdapterSpec
from fd_ontology_kernel.kernel import AdapterDeclaration, Kernel
from fd_ontology_kernel.memory import (
    MemoryJournal,
    MemoryProposalBox,
    MemoryPublicationStore,
    MemoryRegistry,
    MemoryUnitStore,
    memory_vocabulary,
)


class SilentJournal(MemoryJournal):
    def append(self, event):
        return event


class _Vocabulary:
    def __init__(self):
        self._vocabulary = memory_vocabulary()

    def load(self, *, scope_id: str):
        return self._vocabulary


DECLARATION = AdapterDeclaration(
    system="conformance-broken-fixture",
    kernel_contract="1.0",
    manifest_shape=2,
)


def build() -> AdapterSpec:
    def sandbox() -> Kernel:
        return Kernel(
            units=MemoryUnitStore(),
            events=SilentJournal(),
            vocabulary=_Vocabulary(),
            proposals=MemoryProposalBox(),
            publications=MemoryPublicationStore(),
            distribution=MemoryRegistry(),
            relationship_kinds=("belongs_to", "cites_law", "defines"),
            manifest_shape=2,
        )

    return AdapterSpec(
        name="broken",
        system=DECLARATION.system,
        declaration=DECLARATION,
        sandbox=sandbox,
    )
