"""The ontology construction kernel.

Three ontology surfaces exist in this workspace — a per-domain authoring
surface, a per-customer object surface bound to datasets, and a global
vocabulary surface. They share the same invariants and had each implemented
them separately. This package is the single implementation of those
invariants: keys, closed vocabularies, the write path and its append-only
event, the proposal state machine, content digests and versions, the
distribution ledger, and the four-layer ledger projection.

It owns no storage, no configuration and no network. Everything that touches
a database is supplied by the caller through the six ports in ``ports``.
"""

from __future__ import annotations

from .contracts import (
    Evidence,
    Event,
    ManifestEnvelope,
    Proposal,
    Publication,
    Relationship,
    Scope,
    Unit,
)
from .errors import (
    ErrorCode,
    OntologyKernelError,
    ShapeDeclarationMissing,
    ShapeUnknown,
)
from .kernel import Kernel, Written
from .vocab import Entry, Vocabulary, VocabularySet, assert_additive, uri_for

__version__ = "0.1.0"

__all__ = [
    "Entry",
    "ErrorCode",
    "Evidence",
    "Event",
    "Kernel",
    "ManifestEnvelope",
    "OntologyKernelError",
    "Proposal",
    "Publication",
    "Relationship",
    "Scope",
    "ShapeDeclarationMissing",
    "ShapeUnknown",
    "Unit",
    "Vocabulary",
    "VocabularySet",
    "Written",
    "assert_additive",
    "uri_for",
    "__version__",
]
