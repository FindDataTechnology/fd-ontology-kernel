"""The gate: a read-only, per-line, actionable verdict for CI.

Usage::

    python -m fd_ontology_kernel.conformance.gate --list
    python -m fd_ontology_kernel.conformance.gate --adapter memory
    python -m fd_ontology_kernel.conformance.gate --all --scan /path/to/workspace

Exit codes: ``0`` every check passed, ``1`` at least one violation, ``2`` the
gate could not run at all (bad arguments, unreadable registry).

Output discipline: one line per violation, shaped

    <system> · <assertion> · <fact> · 修法：<fix>

so a CI log names what broke, where, and what to change — never a bare "failed".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .spec import DEFAULT_REGISTRY, load_registry, read_registry, scan_unregistered
from .suite import (
    assertion_ids,
    run_all_with_skips,
    self_check,
    validate_registry_of_assertions,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ontology_conformance_gate",
        description="Assert that ontology adapters honour the kernel's invariants.",
    )
    parser.add_argument("--adapter", action="append", default=[],
                        help="registry entry name to check (repeatable)")
    parser.add_argument("--all", action="store_true",
                        help="check every registered adapter")
    parser.add_argument("--list", action="store_true",
                        help="list assertion ids and registered adapters, then exit")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY,
                        help=f"path to adapters.json (default: {DEFAULT_REGISTRY})")
    parser.add_argument("--scan", action="append", default=[], type=Path,
                        help="directory to scan for unregistered kernel implementations")
    parser.add_argument("--skip-self-check", action="store_true",
                        help="do not prove each assertion can fail (not recommended)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # A suite that cannot fail is worse than no suite: check its own integrity first.
    suite_problems = validate_registry_of_assertions()
    if suite_problems:
        for problem in suite_problems:
            print(f"conformance-suite · registry · {problem} · 修法：补齐断言登记")
        return 2

    if args.list:
        # Listing is metadata only: importing every adapter just to print a name
        # turns a plain "what is registered?" into a dependency problem. Whether a
        # module actually imports is what --all (and the gate) is for.
        print("assertions:")
        for assertion_id in assertion_ids():
            print(f"  - {assertion_id}")
        print("adapters:")
        try:
            entries = read_registry(args.registry)
        except Exception as exc:  # noqa: BLE001
            print(f"registry unreadable: {type(exc).__name__}: {exc}")
            return 2
        for entry in entries:
            print(
                f"  - {entry.name} ({entry.system}, shape {entry.manifest_shape})"
                + (f" — {entry.notes}" if entry.notes else "")
            )
        return 0

    if not args.adapter and not args.all:
        print("nothing selected: pass --adapter <name>, --all, or --list", file=sys.stderr)
        return 2

    try:
        specs, violations = load_registry(
            args.registry, only=None if args.all else args.adapter
        )
    except Exception as exc:  # noqa: BLE001
        print(f"registry unreadable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.scan:
        violations.extend(scan_unregistered(args.scan))

    if not args.skip_self_check:
        violations.extend(self_check())

    checks = 0
    skipped: list[tuple[str, str, str]] = []
    for spec in specs:
        found, skips = run_all_with_skips(spec)
        checks += len(assertion_ids()) - len(skips)
        violations.extend(found)
        skipped.extend((spec.system, assertion_id, reason) for assertion_id, reason in skips)

    for system, assertion_id, reason in skipped:
        print(f"SKIP: {system} · {assertion_id} · {reason}")

    for violation in violations:
        print(violation.render())

    if violations:
        print(
            f"\nFAILED: {len(violations)} violation(s) across {checks} check(s)"
            + (f", {len(skipped)} skipped" if skipped else "")
        )
        return 1

    print(
        f"PASS: {checks} check(s) across {len(specs)} adapter(s)"
        + (f", {len(skipped)} skipped" if skipped else "")
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
