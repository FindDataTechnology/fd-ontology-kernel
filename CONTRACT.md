# fd-ontology-kernel contract

The kernel contract is what an adapter promises and what the kernel promises
back. It has four parts: the six ports, the event envelope, the manifest shapes,
and the error codes. Anything not written here is an implementation detail.

Contract version: **1.0**. An adapter declares the version it implements plus the
manifest shape it produces; the kernel refuses to serve a declaration with a
different major version or an unknown shape.

## 1. Ports

An adapter supplies these objects. The kernel never opens a connection, reads a
configuration file, or makes a network request of its own.

```python
class UnitStore(Protocol):
    def get(self, *, scope_id, kind, external_key) -> Unit | None
    def upsert(self, unit: Unit) -> Unit
    def list_live(self, *, scope_id) -> list[Unit]
    def live_key_kinds(self, *, scope_id) -> dict[str, str]

class EventJournal(Protocol):
    def append(self, event: Event) -> Event
    def fold(self, *, scope_id) -> list[Event]
    def count_since(self, *, scope_id, seq=None, since=None) -> int
    # optional:
    def next_seq(self, *, scope_id) -> int

class ProposalBox(Protocol):
    def find_claim(self, *, scope_id, external_key, evidence_digest) -> Proposal | None
    def insert(self, proposal: Proposal) -> Proposal
    def merge_submitters(self, *, proposal_id, submitters) -> Proposal
    def find_by_key(self, *, scope_id, external_key, state=None) -> Proposal | None
    def get(self, *, scope_id, proposal_id) -> Proposal | None
    def decide(self, *, proposal_id, state, decided_by, note="") -> Proposal
    def list_pending(self, *, scope_id, batch=None, limit=200) -> list[Proposal]

class PublicationStore(Protocol):
    def latest(self, *, scope_id) -> Publication | None
    def get(self, *, scope_id, version=None, digest=None) -> Publication | None
    def next_version(self, *, scope_id) -> int
    def insert(self, publication: Publication) -> Publication

class VocabularySource(Protocol):
    def load(self, *, scope_id) -> Vocabulary

class DistributionRegistry(Protocol):
    def find_by_token_hash(self, *, token_hash) -> Consumer | None
    def record_pull(self, *, consumer_id, scope_id, version) -> PullEvent
    def pulls_since(self, *, consumer_id=None, since=None) -> list[PullEvent]
    def consumers(self) -> list[Consumer]
```

Required to write at all: `UnitStore`, `EventJournal`, `VocabularySource`.
Required to publish: `PublicationStore`. Required to nominate/confirm:
`ProposalBox`. Required to distribute: `DistributionRegistry`.

**Transaction discipline.** `Kernel.write()` calls `upsert` then `append`, once.
Both ports must receive a handle on the same transaction. The kernel cannot open
one; if the append fails the adapter's transaction is what undoes the upsert.

## 2. Event envelope

| field | required | meaning |
|---|---|---|
| `scope_id` | yes | the isolation boundary |
| `op` | yes | `create` \| `update` \| `delete` |
| `target_kind` | yes | the unit's kind |
| `target_key` | yes | the semantic external key (portable identity) |
| `actor` | yes | who did it |
| `via` | yes | which path (`authoring`, `proposal`, `console`, `action_gate`, …) |
| `before` | per op | full prior state; forbidden on `create` |
| `after` | per op | full resulting state; forbidden on `delete` |
| `target_ref` | no | the adapter's surrogate id, where it has one |
| `seq` | no | the adapter's per-scope order, where it keeps one |
| `source_ref`, `source_instance_id` | no | fleet / action lineage |
| `occurred_at` | no | adapter timestamp |

There is no update or delete function for events. `events.attempt_mutation()`
exists only to refuse such an attempt with `EVENT_APPEND_ONLY`.

## 3. Manifest shapes

A manifest is `ManifestEnvelope(manifest_schema_version, payload)`. The digest is
`sha256(canonical_json(payload))` where canonical JSON is
`json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
default=<dates at second precision, sets sorted>)`.

The shape version is **never** inside the payload: putting it there would change
every digest ever published.

| version | name | payload | entry identity | order |
|---|---|---|---|---|
| 1 | id-addressed | `{"entities": [...], "relationships": [...]}` | `id` | entities by `id` then name; relationships by the from/to four-tuple then `id` |
| 2 | key-addressed | `{"domain", "template_name", "template_version", "entity_types", "relationship_kinds", "entities", "relationships"}` | `kind` + `external_key` | by `external_key` |

Shape 1 is the customer object surface's current shape; shape 2 is the per-domain
authoring surface's. Payloads must be built from content alone — no timestamps,
no publisher, no version number — or the digest stops being stable.

`tests/golden/` holds digests captured by running each surface's own code. The
kernel must reproduce them byte for byte; that is the compatibility gate.

## 4. Error codes

Every failure the kernel raises itself carries one of these codes.

| code | raised when |
|---|---|
| `KEY_UNFOLDED` | a key arrived without its required parent (a bare clause number) |
| `VOCAB_OUT_OF_SET` | a kind/value outside the scope's closed vocabulary |
| `VOCAB_ENTRY_REMOVED` | a vocabulary release removed or renamed an entry |
| `EVENT_APPEND_ONLY` | an attempt to mutate or delete an appended event |
| `PROPOSAL_STALE_EVIDENCE` | a rejected proposal resubmitted with the same evidence |
| `MANIFEST_SHAPE_UNKNOWN` | a manifest carries a shape version this kernel does not implement |
| `PORT_MISSING` | a required port was not supplied |
| `SHAPE_DECLARATION_MISSING` | an adapter did not declare its kernel/shape versions, or declared an unsupported one |
| `INVARIANT_VIOLATED` | a generic invariant violation with no more specific code |

Codes are additive. Adding one bumps the contract version; removing or changing
the meaning of one requires a major version bump.

## 5. What the kernel deliberately does not do

- It does not own storage, migrations, or transactions.
- It does not read corpus text, dataset columns, or credentials. Verifying that a
  quote appears in a chunk, or that a column exists in a dataset, is the
  adapter's job — the kernel requires only that the evidence *identifies* a
  source and carries a quote.
- It does not know domain vocabulary. Fold rules and "not a concept" predicates
  are registered by a domain pack at import time.
- It does not resolve conflicts between scopes. There is no cross-scope view.
