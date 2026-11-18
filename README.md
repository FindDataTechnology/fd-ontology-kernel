# fd-ontology-kernel

> **Part of the [Base line (寻数·壹座)](https://www.finddatatech.cloud/products/base) of [FindData](https://www.finddatatech.cloud)** — shared ontology mechanisms: one write path, an append-only event trail in the same transaction, closed vocabularies.

The shared mechanisms behind every ontology surface in this workspace: one write
path, an append-only event trail in the same transaction, closed vocabularies,
additive-only releases, content-only digests, and a conformance gate that tells
you which of those your system actually honours.

Standard library only, by design — a third-party dependency here would land in
every consumer's environment (measured 2026-11-10: adding one to a
`sys.path`-imported package broke two consumer repos at import time).

## What it owns, and what it does not

| Owns | Does not own |
| --- | --- |
| the vocabulary gate (a value outside the closed set is refused before anything is written) | your storage, your schema, your migrations |
| write-boundary key folding, and the alias trail it leaves | your HTTP surface, your auth, your background jobs |
| the single write path and its paired event | your transaction API (it calls your ports; your ports open the transaction) |
| canonical JSON, content-only digests, manifest shapes | which shape *you* use — you declare it |
| proposal identity, stale-evidence refusal | your queueing, your UI |
| the ledger projection and its reconciliation facts | your dashboards |

The kernel never opens a connection, reads a config file, or makes a request.

## Five minutes: the six ports

Everything you must provide is one object per port, and the whole surface is
worked through in [`examples/toy_adapter.py`](examples/toy_adapter.py) — six
small classes over a single JSON file. Read that file; it is the documentation.

```python
from fd_ontology_kernel.kernel import AdapterDeclaration, Kernel

DECLARATION = AdapterDeclaration(
    system="my-system",
    kernel_contract="1.0",   # the kernel API generation you speak
    manifest_shape=2,        # 1 = entries addressed by id, 2 = by semantic key
)

kernel = Kernel(
    units=MyUnitStore(session),          # get / upsert / list_live / live_key_kinds
    events=MyJournal(session),           # append / fold / count_since
    vocabulary=MyVocabulary(),           # load(scope_id) -> Vocabulary
    proposals=MyProposalBox(session),    # optional; needed for nominations
    publications=MyPublicationStore(session),  # optional; needed for publishing
    distribution=MyRegistry(),           # optional; needed for consumer pulls
    relationship_kinds=("belongs_to",),  # the closed set for relationships
    clause_kinds=(),                     # non-empty only if you model clauses
    manifest_shape=DECLARATION.manifest_shape,
    declaration=DECLARATION,
)
```

Two things are easy to get wrong, and neither is checked by your type checker:

1. **The journal is append-only.** There is no update or delete method on
   `EventJournal`, and `Event` is frozen. Adding a "fix event" path is adding a
   way for the trail to lie.
2. **The transaction is yours.** `Kernel.write()` calls `UnitStore.upsert` then
   `EventJournal.append`, once. Hand both ports the same open transaction
   (`conn.transaction()`, `Session.begin()`); the kernel cannot open one and will
   not close one. If the pair can land separately, a write exists with no event —
   which is the exact failure the pairing is for.

### Optional extras the suite uses when present

The ports are the contract; a handful of small methods make the checks exact.
They are listed in `examples/toy_adapter.py`:

* `UnitStore.raw_count()` and `PublicationStore.count()` — "opening the ledger
  changed nothing" needs a row-count before and after;
* `EventJournal.next_seq(scope_id)` — if your trail keeps a sequence number, the
  kernel asks you for it instead of inventing one;
* `DistributionRegistry.register(...)` — the pull-trail check needs a consumer.

## Running the gate

```
python scripts/ontology_conformance_gate.py --list
python scripts/ontology_conformance_gate.py --adapter my-system
python scripts/ontology_conformance_gate.py --all
```

Exit codes: `0` every check passed, `1` at least one violation, `2` the gate
could not run. One line per finding, shaped

```
<system> · <assertion> · <fact> · 修法：<fix>
```

so CI names what broke and how to fix it, never a bare "failed".

Register yourself in [`adapters.json`](adapters.json). If your checkout is not
installed in the gate's environment, name it in `paths` (relative to the registry
file) instead of leaving yourself out — an unregistered implementation is
invisible to every check, which is the one blind spot the suite cannot report.

## Skipped is not passed

Some rules are about capabilities a surface may genuinely not have. Declare them:

```python
AdapterSpec(..., capabilities={"proposals": "projection", "distribution": "absent"})
```

* `proposals`: `writable` (a real box), `projection` (a read-only view over
  another system's records), `absent`;
* `distribution`: `present`, `absent`.

A rule about a missing capability is reported as `SKIP: … · <reason>` and is
**never** counted as a pass. Leaving `capabilities` unset means "assume
everything": silence is always checked in full, so no adapter can buy a skip by
saying nothing. A misspelled key or value is itself a violation.

## Manifest shapes

The shape version says how a payload addresses its entries. It lives **next to**
the payload, never inside it — inside would change every digest ever published.

| Shape | Addressing | Used by |
| --- | --- | --- |
| 1 | `entities[].id`, `relationships[].from_entity_id/to_entity_id` | the customer object surface |
| 2 | `entities[].kind + external_key`, `relationships[].from_key/to_key` | the domain authoring surface |

The canonical-JSON recipe (`sort_keys=True`, `separators=(",", ":")`,
`ensure_ascii=False`) is byte-for-byte what both surfaces already used. Changing
it invalidates every published version, so it is written once here and guarded by
golden tests.

## Vocabulary releases are additive

A release may add entries and retire them with `superseded_by`; it may never
delete one or change an id. A deletion silently orphans every instance that used
the value, and the old meaning is gone. `fd_ontology_kernel.vocab.assert_additive`
is the rule; services call it against the previously released set.

## Where to read next

* [`CONTRACT.md`](CONTRACT.md) — port signatures and the invariants in full.
* [`examples/toy_adapter.py`](examples/toy_adapter.py) — the worked adapter.
* `tests/` — the behaviour each invariant is pinned to, including the
  counterexample that proves every assertion can fail.
