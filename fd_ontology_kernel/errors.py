"""The closed set of failure codes the kernel may raise.

Why a closed set: a caller that can only catch ``OntologyKernelError`` cannot
tell "the key was not folded" from "the vocabulary refused this kind", and ends
up logging one string for both. Every code here names a *specific* invariant
violation so a report can say which one, and so the conformance suite can
assert on the code rather than on message text.

Adding a code is a contract change: bump ``KERNEL_CONTRACT_VERSION`` and record
it in ``CONTRACT.md``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class ErrorCode(str, Enum):
    """Every failure the kernel raises itself, named."""

    #: A key arrived without its required parent (a bare clause number).
    KEY_UNFOLDED = "KEY_UNFOLDED"
    #: A kind/value outside the scope's closed vocabulary.
    VOCAB_OUT_OF_SET = "VOCAB_OUT_OF_SET"
    #: A vocabulary release removed an entry that an earlier release had.
    VOCAB_ENTRY_REMOVED = "VOCAB_ENTRY_REMOVED"
    #: An attempt to mutate or delete an already-appended event.
    EVENT_APPEND_ONLY = "EVENT_APPEND_ONLY"
    #: A proposal resubmitted with the same evidence after a decision.
    PROPOSAL_STALE_EVIDENCE = "PROPOSAL_STALE_EVIDENCE"
    #: A manifest carries a shape version this kernel does not implement.
    MANIFEST_SHAPE_UNKNOWN = "MANIFEST_SHAPE_UNKNOWN"
    #: A required port was not supplied.
    PORT_MISSING = "PORT_MISSING"
    #: An adapter did not declare its kernel/shape versions.
    SHAPE_DECLARATION_MISSING = "SHAPE_DECLARATION_MISSING"
    #: A generic invariant violation (used only where no specific code fits).
    INVARIANT_VIOLATED = "INVARIANT_VIOLATED"


class OntologyKernelError(ValueError):
    """Base error: carries a code and structured context, never just a string.

    Subclasses :class:`ValueError` on purpose. Every consumer surface already
    treats "this write is not allowed" as a ``ValueError`` (routes return 4xx on
    it, importers collect it per row), and those call sites must keep working
    while gaining the named code.
    """

    code: ErrorCode = ErrorCode.INVARIANT_VIOLATED

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(f"{self.code.value}: {message}")
        self.message = message
        self.context: Mapping[str, Any] = context

    def __str__(self) -> str:  # pragma: no cover - exercised through base
        return f"{self.code.value}: {self.message}"


class KeyUnfolded(OntologyKernelError):
    code = ErrorCode.KEY_UNFOLDED


class VocabOutOfSet(OntologyKernelError):
    code = ErrorCode.VOCAB_OUT_OF_SET


class VocabEntryRemoved(OntologyKernelError):
    code = ErrorCode.VOCAB_ENTRY_REMOVED


class EventAppendOnly(OntologyKernelError):
    code = ErrorCode.EVENT_APPEND_ONLY


class ProposalStaleEvidence(OntologyKernelError):
    code = ErrorCode.PROPOSAL_STALE_EVIDENCE


class ShapeUnknown(OntologyKernelError):
    code = ErrorCode.MANIFEST_SHAPE_UNKNOWN


class PortMissing(OntologyKernelError):
    code = ErrorCode.PORT_MISSING


class ShapeDeclarationMissing(OntologyKernelError):
    code = ErrorCode.SHAPE_DECLARATION_MISSING


class InvariantViolated(OntologyKernelError):
    code = ErrorCode.INVARIANT_VIOLATED


#: Code -> exception class, so callers (and the conformance gate) can map back.
BY_CODE: dict[ErrorCode, type[OntologyKernelError]] = {
    cls.code: cls
    for cls in (
        KeyUnfolded,
        VocabOutOfSet,
        VocabEntryRemoved,
        EventAppendOnly,
        ProposalStaleEvidence,
        ShapeUnknown,
        PortMissing,
        ShapeDeclarationMissing,
        InvariantViolated,
    )
}
