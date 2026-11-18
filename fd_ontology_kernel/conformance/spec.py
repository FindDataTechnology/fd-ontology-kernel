"""Adapter specifications, violations, and the adapter registry.

A registry entry names a factory that returns an :class:`AdapterSpec`. The spec's
``sandbox()`` must return a **fresh, throwaway** kernel: the gate exercises the
write path, the proposal box and the publication store, and it is not allowed to
write business data. Making the sandbox a factory rather than a kernel is what
forces that property structurally — a shared production connection cannot be
handed in by accident.

Drift is reported, never skipped: an entry whose module will not import, whose
factory returns something else, or whose declaration disagrees with the registry
is a violation. So is an implementation found in a repo but absent from the
registry (see :func:`scan_unregistered`).
"""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from ..errors import OntologyKernelError
from ..kernel import AdapterDeclaration, Kernel, validate_declaration

#: Default registry location (next to the package, at the kernel repo root).
DEFAULT_REGISTRY = Path(__file__).resolve().parents[2] / "adapters.json"


@dataclass(frozen=True)
class AdapterSpec:
    """One system's claim that it implements the kernel, with a throwaway factory."""

    name: str
    system: str
    declaration: AdapterDeclaration
    sandbox: Callable[[], Kernel]
    notes: str = ""
    #: What this surface can actually do, where the kernel's ports allow more than
    #: the surface has. Keys and accepted values are fixed so a typo cannot quietly
    #: widen the skip surface:
    #:
    #: * ``"proposals"``: ``"writable"`` (a real box), ``"projection"`` (read-only
    #:   view over another system's records), ``"absent"``.
    #: * ``"distribution"``: ``"present"``, ``"absent"``.
    #:
    #: **``None`` means "assume everything"**: an adapter that does not describe
    #: itself gets checked as if it had every capability, which is the safe default
    #: (a missing declaration never turns a rule into a skip). A capability that is
    #: declared absent turns its assertions into reported skips — never passes.
    capabilities: Optional[Mapping[str, str]] = None


#: Accepted ``capabilities`` keys and values. Anything else is a violation, because
#: an unrecognised capability would silently match nothing and skip nothing.
CAPABILITIES: Mapping[str, tuple[str, ...]] = {
    "proposals": ("writable", "projection", "absent"),
    "distribution": ("present", "absent"),
}


def capabilities_problems(
    capabilities: Optional[Mapping[str, str]],
) -> list[str]:
    """Declared capabilities that this gate does not understand.

    A typo (``"proposal"``, ``"read-only"``) must not behave like a capability
    nobody asked about: unknown keys and unknown values are both violations, so
    the skip surface cannot be widened by accident.
    """
    if capabilities is None:
        return []
    problems: list[str] = []
    for name, value in capabilities.items():
        allowed = CAPABILITIES.get(str(name))
        if allowed is None:
            problems.append(
                f"unknown capability {name!r}; known: {sorted(CAPABILITIES)}"
            )
            continue
        if value not in allowed:
            problems.append(
                f"capability {name!r} has value {value!r}; allowed: {list(allowed)}"
            )
    return problems


@dataclass(frozen=True)
class Violation:
    """One failed check, in the shape an operator can act on."""

    system: str
    assertion_id: str
    fact: str
    fix: str

    def render(self) -> str:
        return f"{self.system} · {self.assertion_id} · {self.fact} · 修法：{self.fix}"


@dataclass(frozen=True)
class RegistryEntry:
    name: str
    system: str
    module: str
    factory: str
    kernel_contract: str
    manifest_shape: int
    notes: str = ""
    #: Extra import roots, relative to the registry file. A surface that is not
    #: installed in the gate's environment (a sibling checkout) is named here
    #: instead of being silently skipped.
    paths: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)


def read_registry(path: Optional[Path] = None) -> list[RegistryEntry]:
    """Parse ``adapters.json``; a broken file is an error, not an empty list."""
    target = Path(path) if path is not None else DEFAULT_REGISTRY
    data = json.loads(target.read_text(encoding="utf-8"))
    entries = data.get("adapters")
    if not isinstance(entries, list):
        raise ValueError(f"{target} has no 'adapters' array")
    out: list[RegistryEntry] = []
    for raw in entries:
        if not isinstance(raw, Mapping):
            raise ValueError(f"{target}: every adapter entry must be an object")
        missing = [k for k in ("name", "system", "module", "factory",
                               "kernel_contract", "manifest_shape") if k not in raw]
        if missing:
            raise ValueError(f"{target}: adapter entry missing {missing}: {raw}")
        out.append(
            RegistryEntry(
                name=str(raw["name"]),
                system=str(raw["system"]),
                module=str(raw["module"]),
                factory=str(raw["factory"]),
                kernel_contract=str(raw["kernel_contract"]),
                manifest_shape=int(raw["manifest_shape"]),
                notes=str(raw.get("notes") or ""),
                paths=tuple(str(p) for p in (raw.get("paths") or ())),
                extra={k: v for k, v in raw.items()
                       if k not in {"name", "system", "module", "factory",
                                    "kernel_contract", "manifest_shape", "notes",
                                    "paths"}},
            )
        )
    return out


def load_adapter(
    entry: RegistryEntry,
    *,
    base_dir: Optional[Path] = None,
) -> tuple[Optional[AdapterSpec], list[Violation]]:
    """Import the factory and validate what it returned.

    ``base_dir`` is the registry file's directory; entry ``paths`` are resolved
    against it and appended to ``sys.path`` so a sibling checkout can be checked
    without installing it.

    Returns ``(spec, violations)``. A missing or unusable entry yields
    ``(None, [...])`` — it is never silently skipped and never counted as a pass.
    """
    violations: list[Violation] = []
    for raw_path in entry.paths:
        resolved = (base_dir or Path.cwd()) / raw_path
        if not resolved.is_dir():
            violations.append(Violation(
                system=entry.system,
                assertion_id="registry:path",
                fact=f"registered import path {raw_path!r} does not exist at {resolved}",
                fix="改正 adapters.json 的 paths（相对 registry 文件所在目录）",
            ))
            continue
        if str(resolved) not in sys.path:
            sys.path.insert(0, str(resolved))

    try:
        module = importlib.import_module(entry.module)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        violations.append(Violation(
            system=entry.system,
            assertion_id="registry:importable",
            fact=f"module {entry.module!r} could not be imported: "
                 f"{type(exc).__name__}: {exc}",
            fix="登记项指向的模块必须可导入；修正 module 路径或让该模块在闸门环境中可安装",
        ))
        return None, violations

    factory = getattr(module, entry.factory, None)
    if factory is None or not callable(factory):
        violations.append(Violation(
            system=entry.system,
            assertion_id="registry:entrypoint",
            fact=f"module {entry.module!r} has no callable {entry.factory!r}",
            fix="工厂必须返回 AdapterSpec；检查 adapters.json 里登记的 factory 名",
        ))
        return None, violations

    try:
        produced = factory()
    except Exception as exc:  # noqa: BLE001
        violations.append(Violation(
            system=entry.system,
            assertion_id="registry:entrypoint",
            fact=f"factory {entry.module}:{entry.factory} raised "
                 f"{type(exc).__name__}: {exc}",
            fix="工厂必须返回 AdapterSpec；让它只需构造对象，不做 I/O",
        ))
        return None, violations

    if not isinstance(produced, AdapterSpec):
        violations.append(Violation(
            system=entry.system,
            assertion_id="registry:shape",
            fact=f"factory returned {type(produced).__name__}, expected AdapterSpec",
            fix="工厂返回 AdapterSpec(name, system, declaration, sandbox)",
        ))
        return None, violations

    if produced.name != entry.name or produced.system != entry.system:
        violations.append(Violation(
            system=entry.system,
            assertion_id="registry:drift",
            fact=f"registry says name={entry.name!r} system={entry.system!r}, "
                 f"factory says name={produced.name!r} system={produced.system!r}",
            fix="以工厂声明为准修正 adapters.json（或反之），不要让两者不一致",
        ))

    if produced.declaration.kernel_contract != entry.kernel_contract:
        violations.append(Violation(
            system=entry.system,
            assertion_id="registry:drift",
            fact=f"registry declares kernel_contract={entry.kernel_contract!r}, "
                 f"factory declares {produced.declaration.kernel_contract!r}",
            fix="registry 与工厂的内核契约版本必须一致",
        ))

    if produced.declaration.manifest_shape != entry.manifest_shape:
        violations.append(Violation(
            system=entry.system,
            assertion_id="registry:drift",
            fact=f"registry declares manifest_shape={entry.manifest_shape}, "
                 f"factory declares {produced.declaration.manifest_shape}",
            fix="registry 与工厂的 manifest 形状版本必须一致",
        ))

    for problem in capabilities_problems(produced.capabilities):
        violations.append(Violation(
            system=entry.system,
            assertion_id="registry:capabilities",
            fact=problem,
            fix="capabilities 只认固定键值（见 spec.CAPABILITIES）；写错等于声明无效",
        ))

    try:
        validate_declaration(produced.declaration)
    except OntologyKernelError as exc:
        violations.append(Violation(
            system=entry.system,
            assertion_id="registry:declaration",
            fact=str(exc),
            fix="适配方必须声明受支持的内核契约版本与 manifest 形状版本",
        ))

    # The registry's own claim must be checked too: a stale entry can say the
    # adapter speaks a version nobody supports while the factory still answers.
    try:
        validate_declaration(AdapterDeclaration(
            system=entry.system,
            kernel_contract=entry.kernel_contract,
            manifest_shape=entry.manifest_shape,
        ))
    except OntologyKernelError as exc:
        violations.append(Violation(
            system=entry.system,
            assertion_id="registry:declaration",
            fact=f"adapters.json declares an unsupported contract/shape: {exc}",
            fix="把 adapters.json 的 kernel_contract / manifest_shape 改成受支持的值",
        ))

    return produced, violations


def load_registry(
    path: Optional[Path] = None,
    *,
    only: Optional[Iterable[str]] = None,
) -> tuple[list[AdapterSpec], list[Violation]]:
    """Load every entry (or just ``only``), returning specs plus load violations."""
    target = Path(path) if path is not None else DEFAULT_REGISTRY
    wanted = set(only) if only is not None else None
    specs: list[AdapterSpec] = []
    violations: list[Violation] = []
    for entry in read_registry(target):
        if wanted is not None and entry.name not in wanted:
            continue
        spec, problems = load_adapter(entry, base_dir=target.parent)
        violations.extend(problems)
        if spec is not None:
            specs.append(spec)
    if wanted is not None:
        found = {spec.name for spec in specs}
        for name in sorted(wanted - found):
            violations.append(Violation(
                system=name,
                assertion_id="registry:present",
                fact=f"no registry entry named {name!r}",
                fix="把该系统登记进 adapters.json，或改用 --list 查看可用名",
            ))
    return specs, violations


def scan_unregistered(roots: Iterable[Path]) -> list[Violation]:
    """Find files that import the kernel under a repo not present in the registry.

    This is the "implemented but unregistered" check: a system that wires the
    kernel but never registered is invisible to every other check, which is
    precisely the blind spot the spec forbids.
    """
    registered_systems = {entry.system for entry in read_registry()}
    registered_modules = {entry.module for entry in read_registry()}
    violations: list[Violation] = []
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if any(part in {".venv", "venv", "__pycache__", "node_modules", ".git"}
                   for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "fd_ontology_kernel" not in text:
                continue
            # The package's own files are not an adapter of some other system.
            if "fd_ontology_kernel" in path.parts:
                continue
            system = path.relative_to(base).parts[0] if len(path.relative_to(base).parts) > 1 else base.name
            if path.stem in {"conftest", "setup"}:
                continue
            if system in registered_systems or base.name in registered_systems:
                continue
            violations.append(Violation(
                system=system,
                assertion_id="registry:unregistered",
                fact=f"{path} imports fd_ontology_kernel but {system!r} is not in adapters.json",
                fix="把该系统登记进 adapters.json 并提供 sandbox 工厂；未登记的实现无法被其余断言覆盖",
            ))
    # One violation per system, not per file.
    seen: dict[str, Violation] = {}
    for violation in violations:
        seen.setdefault(violation.system, violation)
    return list(seen.values())
