"""The conformance suite: what any adapter must prove before it may claim the kernel.

An adapter is a set of six ports wired to a real store. The kernel's unit tests
prove the *mechanism* is right; this package proves an *adapter* has not
undermined it — a permissive vocabulary, a journal that silently drops events, a
proposal box that writes content directly, a category error in the pull trail.

Every assertion here is registered together with a counterexample: a broken
setup on which the assertion MUST fail. An assertion that has never failed is an
assertion nobody can trust, and this workspace has been burned by exactly that
more than once.
"""

from __future__ import annotations

from . import assertions as assertions
from .spec import AdapterSpec, Violation, load_registry
from .suite import ASSERTIONS, assertion_ids, run_all

__all__ = [
    "ASSERTIONS",
    "AdapterSpec",
    "Violation",
    "assertion_ids",
    "load_registry",
    "run_all",
]
