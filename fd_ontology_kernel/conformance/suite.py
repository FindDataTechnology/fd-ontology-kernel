"""Running the suite: one adapter in, violations out.

The suite is deliberately tiny. Its whole value is that it refuses to run an
assertion that has no counterexample, and that it isolates each assertion behind
a fresh sandbox so a failure cannot be explained by a previous check.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .assertions import ASSERTIONS, BY_ID, Assertion
from .spec import AdapterSpec, Violation


def assertion_ids() -> list[str]:
    return [assertion.id for assertion in ASSERTIONS]


def validate_registry_of_assertions() -> list[str]:
    """Every assertion must carry a counterexample; an unproven one is not admitted."""
    problems: list[str] = []
    for assertion in ASSERTIONS:
        if assertion.counterexample is None:
            problems.append(f"{assertion.id}: no counterexample")
        if not assertion.invariant.strip():
            problems.append(f"{assertion.id}: no invariant named")
        if not assertion.fix.strip():
            problems.append(f"{assertion.id}: no fix given")
    ids = assertion_ids()
    if len(ids) != len(set(ids)):
        problems.append("duplicate assertion ids")
    return problems


def run_assertion(spec: AdapterSpec, assertion: Assertion) -> list[Violation]:
    """Run one assertion. An exception is a violation, never a pass."""
    try:
        facts = assertion.run(spec)
    except Exception as exc:  # noqa: BLE001 - an exploding check is a failed check
        return [Violation(
            system=spec.system,
            assertion_id=assertion.id,
            fact=f"assertion raised {type(exc).__name__}: {exc}",
            fix=assertion.fix,
        )]
    return [
        Violation(system=spec.system, assertion_id=assertion.id, fact=fact, fix=assertion.fix)
        for fact in facts
    ]


def applicability(spec: AdapterSpec, assertion: Assertion) -> Optional[str]:
    """Why this assertion cannot apply here, or ``None`` when it can.

    A failure to *decide* applicability is itself a violation-shaped fact: it is
    returned as a reason so the gate reports the assertion as skipped-with-error
    rather than silently passing it.
    """
    if assertion.applicable is None:
        return None
    try:
        return assertion.applicable(spec)
    except Exception as exc:  # noqa: BLE001
        return f"applicability could not be decided: {type(exc).__name__}: {exc}"


def run_all_with_skips(
    spec: AdapterSpec,
    *,
    only: Optional[Iterable[str]] = None,
) -> tuple[list[Violation], list[tuple[str, str]]]:
    """Run the suite, returning ``(violations, skips)``.

    A skip is a *distinct* outcome: the adapter has no such concept, so the rule
    is vacuous — it is not evidence that the rule holds. Callers must render it
    as a skip, never as a pass.
    """
    wanted = set(only) if only is not None else None
    violations: list[Violation] = []
    skips: list[tuple[str, str]] = []
    for assertion in ASSERTIONS:
        if wanted is not None and assertion.id not in wanted:
            continue
        reason = applicability(spec, assertion)
        if reason is not None:
            skips.append((assertion.id, reason))
            continue
        violations.extend(run_assertion(spec, assertion))
    if wanted is not None:
        known = set(assertion_ids())
        for missing in sorted(wanted - known):
            violations.append(Violation(
                system=spec.system,
                assertion_id="suite:present",
                fact=f"no assertion named {missing!r}",
                fix="检查断言名拼写；用 --list 查看套件里的断言",
            ))
    return violations, skips


def run_all(
    spec: AdapterSpec,
    *,
    only: Optional[Iterable[str]] = None,
) -> list[Violation]:
    """Run every applicable assertion (or a named subset) against one adapter."""
    violations, _skips = run_all_with_skips(spec, only=only)
    return violations


def self_check() -> list[Violation]:
    """Prove the suite can fail: every counterexample must trip its assertion.

    Returns violations describing any assertion that passed on a broken sample.
    An empty list means every registered assertion has been shown to be capable of
    failing — which is the only evidence that the gate is not decorative.
    """
    violations: list[Violation] = []
    for assertion in ASSERTIONS:
        try:
            caught = bool(assertion.counterexample())
        except Exception as exc:  # noqa: BLE001
            violations.append(Violation(
                system="conformance-suite",
                assertion_id=assertion.id,
                fact=f"counterexample itself raised {type(exc).__name__}: {exc}",
                fix="反例必须构造一个必然违规的样本并跑同一检查",
            ))
            continue
        if not caught:
            violations.append(Violation(
                system="conformance-suite",
                assertion_id=assertion.id,
                fact="the assertion passed on its own counterexample (it cannot fail)",
                fix="修正该断言，或换一个能真正触发违规的反例；空转断言不得入册",
            ))
    return violations
