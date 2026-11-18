"""Manifests, canonical JSON, and content digests.

Two surfaces publish snapshots and both need the same three properties:

1. **Content-only digest.** Timestamps, the publisher, and the version number
   never enter the payload, so identical content always yields the identical
   digest and re-publishing is idempotent.
2. **Order-insensitivity.** Entries are sorted before hashing, so list order
   cannot change a digest.
3. **A shape version.** The payload's *shape* differs between surfaces (one
   addresses entries by surrogate id, the other by semantic key). The version is
   recorded next to the payload — never inside it, which would change every
   digest ever published.

The canonical-JSON recipe (``sort_keys=True``, ``separators=(",", ":")``,
``ensure_ascii=False``) is byte-for-byte the one both surfaces already used.
Changing it would invalidate every existing version, so it is written once here
and guarded by golden tests.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional

from .contracts import ManifestEnvelope
from .errors import ShapeUnknown

# ── shape registry ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ShapeSpec:
    """One manifest shape: its version, a name, and its normalizer."""

    version: int
    name: str
    description: str


#: Shape 1 — id-addressed. The customer object surface's current shape: entries
#: carry their surrogate id and relationships point at entity ids.
SHAPE_ID_ADDRESSED = 1
#: Shape 2 — key-addressed. The per-domain authoring surface's current shape:
#: entries carry ``kind`` + ``external_key`` and relationships carry endpoint
#: keys, so a snapshot can be rebuilt in another environment.
SHAPE_KEY_ADDRESSED = 2

SUPPORTED_SHAPES: dict[int, ShapeSpec] = {
    SHAPE_ID_ADDRESSED: ShapeSpec(
        version=SHAPE_ID_ADDRESSED,
        name="id-addressed",
        description="entities keyed by surrogate id; relationships point at entity ids",
    ),
    SHAPE_KEY_ADDRESSED: ShapeSpec(
        version=SHAPE_KEY_ADDRESSED,
        name="key-addressed",
        description="entities keyed by kind+external_key; relationships carry endpoint keys",
    ),
}


def shape_spec(version: int) -> ShapeSpec:
    """Look up a shape, refusing versions this kernel cannot interpret."""
    try:
        return SUPPORTED_SHAPES[int(version)]
    except (KeyError, TypeError, ValueError):
        raise ShapeUnknown(
            f"manifest shape version {version!r} is not implemented by this kernel; "
            f"supported: {sorted(SUPPORTED_SHAPES)}",
            requested=version,
            supported=sorted(SUPPORTED_SHAPES),
        ) from None


# ── canonical JSON ───────────────────────────────────────────────────────────


def _iso(value: date | datetime) -> str:
    """Date/time → ISO8601 at second precision (naive treated as UTC)."""
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    return value.isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return _iso(value)
    if isinstance(value, (set, frozenset)):
        return sorted(str(item) for item in value)
    raise TypeError(f"manifest contains a value that cannot be serialized: {type(value).__name__}")


def canonical_json(payload: Mapping[str, Any]) -> str:
    """The canonical text a digest is taken over. Byte-stable, forever."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def digest_of(payload: Mapping[str, Any]) -> str:
    """sha256 over the canonical JSON of the payload (shape version excluded)."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# ── entry normalization ──────────────────────────────────────────────────────


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _as_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [str(item) for item in value]


def _as_notes(value: Any) -> Optional[dict[str, str]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return {str(item): "" for item in value}
    return {str(key): str(note) for key, note in value.items()}


def normalize_entity_v1(raw: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """One entity entry → the id-addressed canonical entry.

    Every known key appears, with missing values normalized to ``null``/``[]``.
    Snapshots taken before a column existed therefore normalize to the same shape
    as later ones instead of producing phantom diffs.
    """
    data = dict(raw or {})
    return {
        "id": _as_int(data.get("id")),
        "name": _as_text(data.get("name")) or "",
        "dataset_id": _as_text(data.get("dataset_id")) or "",
        "pk_columns": _as_text_list(data.get("pk_columns")),
        "description": _as_text(data.get("description")) or None,
        "display_column": _as_text(data.get("display_column")) or None,
        "column_notes": _as_notes(data.get("column_notes")),
        "tier": _as_text(data.get("tier")) or None,
        "sensitivity": _as_text(data.get("sensitivity")) or None,
    }


def normalize_relationship_v1(raw: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """One relationship entry → the id-addressed canonical entry."""
    data = dict(raw or {})
    return {
        "id": _as_int(data.get("id")),
        "from_entity_id": _as_int(data.get("from_entity_id")),
        "from_column": _as_text(data.get("from_column")) or "",
        "to_entity_id": _as_int(data.get("to_entity_id")),
        "to_column": _as_text(data.get("to_column")) or "",
        "kind": _as_text(data.get("kind")) or "",
        "declared_kind": _as_text(data.get("declared_kind")),
        "label": _as_text(data.get("label")) or None,
        "junction_dataset_id": _as_text(data.get("junction_dataset_id")),
        "junction_source_column": _as_text(data.get("junction_source_column")),
        "junction_target_column": _as_text(data.get("junction_target_column")),
        "tier": _as_text(data.get("tier")) or None,
        "sensitivity": _as_text(data.get("sensitivity")) or None,
    }


def normalize_entry_v2(
    raw: Optional[Mapping[str, Any]], *, relationship: bool = False
) -> dict[str, Any]:
    """One entry → the key-addressed canonical entry."""
    data = dict(raw or {})
    entry: dict[str, Any] = {
        "kind": _as_text(data.get("kind")) or "",
        "external_key": _as_text(data.get("external_key")) or "",
        "body": _as_text(data.get("body")) or "",
        "tier": _as_text(data.get("tier")) or None,
        "sensitivity": _as_text(data.get("sensitivity")) or None,
        "attrs": dict(data.get("attrs") or {}),
    }
    if relationship:
        entry["from_key"] = _as_text(data.get("from_key")) or None
        entry["to_key"] = _as_text(data.get("to_key")) or None
    return entry


# ── payload normalization ────────────────────────────────────────────────────


def _sort_entities_v1(entries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    def key(item: Mapping[str, Any]) -> tuple:
        entity_id = item.get("id")
        if entity_id is not None:
            return (0, int(entity_id), "")
        return (1, 0, str(item.get("name") or ""))

    return sorted((normalize_entity_v1(e) for e in entries), key=key)


def _sort_relationships_v1(entries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    def num(value: Any) -> int:
        return int(value) if isinstance(value, int) else -1

    def key(item: Mapping[str, Any]) -> tuple:
        return (
            num(item.get("from_entity_id")),
            str(item.get("from_column") or ""),
            num(item.get("to_entity_id")),
            str(item.get("to_column") or ""),
            num(item.get("id")),
        )

    return sorted((normalize_relationship_v1(r) for r in entries), key=key)


def normalize_payload_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Id-addressed payload: ``{"entities": [...], "relationships": [...]}``."""
    data = dict(payload or {})
    return {
        "entities": _sort_entities_v1(data.get("entities") or []),
        "relationships": _sort_relationships_v1(data.get("relationships") or []),
    }


def normalize_payload_v2(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Key-addressed payload: domain/template header plus key-sorted entries."""
    data = dict(payload or {})
    entities = [
        normalize_entry_v2(e)
        for e in (data.get("entities") or [])
    ]
    relationships = [
        normalize_entry_v2(r, relationship=True)
        for r in (data.get("relationships") or [])
    ]
    return {
        "domain": _as_text(data.get("domain")) or "",
        "template_name": _as_text(data.get("template_name")) or "",
        "template_version": _as_int(data.get("template_version")) or 0,
        "entity_types": _as_text_list(data.get("entity_types")),
        "relationship_kinds": _as_text_list(data.get("relationship_kinds")),
        "entities": sorted(entities, key=lambda e: e["external_key"]),
        "relationships": sorted(relationships, key=lambda e: e["external_key"]),
    }


_NORMALIZERS = {
    SHAPE_ID_ADDRESSED: normalize_payload_v1,
    SHAPE_KEY_ADDRESSED: normalize_payload_v2,
}


def canonical_payload(*, shape: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a payload to its shape's canonical form (or refuse the shape)."""
    spec = shape_spec(shape)
    return _NORMALIZERS[spec.version](payload)


def build_payload(
    *,
    shape: int,
    entities: Iterable[Mapping[str, Any]] = (),
    relationships: Iterable[Mapping[str, Any]] = (),
    header: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble a canonical payload from rows plus an optional header block."""
    raw: dict[str, Any] = dict(header or {})
    raw["entities"] = list(entities)
    raw["relationships"] = list(relationships)
    return canonical_payload(shape=shape, payload=raw)


def envelope(*, shape: int, payload: Mapping[str, Any]) -> ManifestEnvelope:
    """Wrap a canonical payload with its shape version (version outside payload)."""
    return ManifestEnvelope(
        manifest_schema_version=shape_spec(shape).version,
        payload=canonical_payload(shape=shape, payload=payload),
    )


def digest_for(*, shape: int, payload: Mapping[str, Any]) -> str:
    """Normalize then digest — the only order that yields stable digests."""
    return digest_of(canonical_payload(shape=shape, payload=payload))


def is_empty(payload: Mapping[str, Any]) -> bool:
    return not (
        (payload or {}).get("entities") or (payload or {}).get("relationships")
    )


# ── diff ─────────────────────────────────────────────────────────────────────


def _identity(item: Mapping[str, Any], keys: tuple[str, ...]) -> tuple:
    return tuple(item.get(k) for k in keys)


def diff_manifests(
    old: Optional[Mapping[str, Any]],
    new: Mapping[str, Any],
    *,
    identity: tuple[str, ...] = ("kind", "external_key"),
    fields: Optional[tuple[str, ...]] = None,
) -> dict[str, list]:
    """Added / removed / changed entries between two canonical payloads.

    ``identity`` names the fields that make an entry the same entry; the default
    is the key-addressed shape's identity. For the id-addressed shape pass
    ``identity=("id",)``. A duplicate identity means corrupt content and is
    reported rather than silently collapsed.
    """
    def index(payload: Optional[Mapping[str, Any]]) -> dict[tuple, dict[str, Any]]:
        out: dict[tuple, dict[str, Any]] = {}
        body = (payload or {}) if isinstance(payload, Mapping) else {}
        for section in ("entities", "relationships"):
            for item in body.get(section) or []:
                key = _identity(item, identity)
                if key in out:
                    raise ValueError(f"duplicate {identity} in manifest: {key!r}")
                out[key] = {**item, "_section": section}
        return out

    before, after = index(old), index(new)
    added = [after[k] for k in after.keys() - before.keys()]
    removed = [before[k] for k in before.keys() - after.keys()]
    changed = []
    for key in before.keys() & after.keys():
        left, right = before[key], after[key]
        compare = fields or tuple(
            sorted((set(left) | set(right)) - set(identity) - {"_section"})
        )
        diffs = {name: [left.get(name), right.get(name)] for name in compare
                 if left.get(name) != right.get(name)}
        if diffs:
            changed.append({"identity": list(key), "section": right["_section"], "fields": diffs})

    def sort_key(item: Mapping[str, Any]) -> tuple:
        return tuple(str(item.get(name)) for name in identity)

    return {
        "added": sorted(added, key=sort_key),
        "removed": sorted(removed, key=sort_key),
        "changed": sorted(changed, key=lambda c: tuple(str(p) for p in c["identity"])),
    }
