"""The reference adapter: the in-memory kernel, used by the gate's self-test.

It exists so the gate has at least one adapter that is known-good: if this one
fails, the kernel or the suite is broken, not a customer system. It also doubles
as the worked example an adapter author copies.
"""

from __future__ import annotations

from ..kernel import AdapterDeclaration
from ..memory import memory_kernel
from .spec import AdapterSpec

DECLARATION = AdapterDeclaration(
    system="fd-ontology-kernel",
    kernel_contract="1.0",
    manifest_shape=2,
)


def build() -> AdapterSpec:
    """Fresh in-memory kernel per call — a sandbox, never shared state."""
    return AdapterSpec(
        name="memory",
        system=DECLARATION.system,
        declaration=DECLARATION,
        sandbox=lambda: memory_kernel(manifest_shape=DECLARATION.manifest_shape),
        notes="in-memory reference adapter; every write is discarded with the kernel",
    )
