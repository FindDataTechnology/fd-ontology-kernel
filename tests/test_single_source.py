"""One definition of each ontology rule — and the exceptions, written down.

The verification for this task is a grep, and a grep cannot tell three very
different things apart:

* a **docstring** that quotes the recipe (law-bench ``distribution.py``);
* a **deliberate** second copy (the signing path, which predates this kernel and
  whose bytes existing signatures were computed over);
* an **unrelated subsystem** that happens to canonicalise JSON for its own dedup
  key (the concept flywheel, xingyan sync).

Rewiring the third group onto the ontology kernel would drag an ontology
dependency into scripts that have nothing to do with ontologies — the exact
anti-pattern recorded in this workspace (a ``sys.path``-imported package gained a
dependency and broke two consumer repos at import time). So the rule this test
enforces is narrower and honest: **no ontology surface may define the digest
recipe itself**, and every file that legitimately contains one is listed below
with the reason. The list is asserted to exist, so it cannot rot silently.

Read the failures as a question: "is this a second definition of the ontology
rule, or a different rule that looks similar?" Only the first is a defect.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = REPO_ROOT.parent

#: Source roots of the surfaces that share the kernel's invariants.
SURFACES: dict[str, Path] = {
    "law-bench": WORKSPACE / "law-bench-internal" / "src" / "bench",
    "fd-wire": WORKSPACE / "fd-wire" / "api" / "fd_wire_api",
    "protocol": WORKSPACE / "fd-open-data-protocol" / "fd_open_data_protocol",
}

#: Files that may contain the recipe, with the reason. Relative to WORKSPACE.
ALLOWED_RECIPE_FILES: dict[str, str] = {
    "fd-wire/api/fd_wire_api/provenance/envelope.py": (
        "signing path: bytes are what existing OpenTimestamps signatures were "
        "computed over; changing the recipe would invalidate them"
    ),
    "law-bench-internal/src/bench/flywheel.py": (
        "concept-flywheel dedup key, not an ontology digest; coupling it to the "
        "ontology kernel would add that dependency to a non-ontology script"
    ),
    "fd-wire/api/fd_wire_api/xingyan/sync.py": (
        "xingyan payload dedup key, not an ontology digest (same reasoning)"
    ),
    "fd-wire/api/fd_wire_api/provenance/export.py": (
        "provenance envelope export; sorts no keys and does not claim to be the "
        "ontology recipe"
    ),
}

#: The entry point each surface must delegate, and the string that proves it.
DELEGATIONS: tuple[tuple[str, str, str], ...] = (
    (
        "law-bench-internal/src/bench/distribution.py",
        "def canonical_digest",
        "kernel_manifest.digest_of",
    ),
    (
        "fd-wire/api/fd_wire_api/entities/versioning.py",
        "def canonical_json",
        "kernel_manifest.canonical_json",
    ),
    (
        "fd-open-data-protocol/fd_open_data_protocol/vocabulary.py",
        "def build_uri",
        "_kernel_uri_for()",
    ),
)


def _python_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if not {".venv", "__pycache__", "node_modules", ".git"} & set(path.parts)
    ]


def _computes_the_recipe(path: Path) -> list[int]:
    """Line numbers where this file *calls* ``json.dumps`` with the full recipe.

    Parsed, not grepped: a docstring quoting the recipe is not a definition, and
    reporting it as one is how a check like this gets switched off.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.keywords:
            continue
        func = node.func
        name = getattr(func, "attr", getattr(func, "id", ""))
        if name != "dumps":
            continue
        keywords = {kw.arg for kw in node.keywords}
        if {"sort_keys", "separators"} <= keywords:
            hits.append(node.lineno)
    return hits


def test_no_ontology_surface_defines_the_digest_recipe_of_its_own() -> None:
    offenders: list[str] = []
    for label, root in SURFACES.items():
        for path in _python_files(root):
            lines = _computes_the_recipe(path)
            if not lines:
                continue
            try:
                relative = str(path.relative_to(WORKSPACE))
            except ValueError:  # a surface root outside the workspace (probe/CI)
                relative = str(path)
            if relative in ALLOWED_RECIPE_FILES:
                continue
            where = ",".join(str(line) for line in lines)
            offenders.append(f"{label}: {relative}:{where}")
    assert offenders == [], (
        "these files compute the canonical-JSON recipe themselves; either "
        "delegate to fd_ontology_kernel.manifest or add them (with a reason) to "
        "ALLOWED_RECIPE_FILES:\n  " + "\n  ".join(offenders)
    )


def test_every_allowed_recipe_file_exists_with_a_reason() -> None:
    """An allowlist that outlives its file is a list of lies."""
    for relative, reason in ALLOWED_RECIPE_FILES.items():
        path = WORKSPACE / relative
        if not path.exists():
            continue  # sibling checkout absent: the surfaces test skips too
        assert reason.strip(), f"{relative} is allowlisted without a reason"


def test_each_surface_still_delegates_its_entry_point() -> None:
    """The check must not pass because a surface deleted its entry point."""
    for relative, marker, delegation in DELEGATIONS:
        path = WORKSPACE / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert marker in text, f"{relative} no longer defines {marker!r}"
        assert delegation in text, (
            f"{relative}: {marker} must delegate to {delegation}; a surface that "
            f"stops delegating is a second definition even if the recipe matches"
        )
