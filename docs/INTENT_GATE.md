# Intent Gate v0 — Deterministic Active Contract Ledger

Status: implemented
Date: 2026-10-05

## Purpose

Intent Gate prevents long-running coding work from silently drifting away from
the project's currently binding intent.

It is not a planning framework and does not run in the CTF solver hot loop. It
only resolves what is binding, controls accepted contract changes, renders a
small task brief, and checks completion against the exact contract revision.

Conversation history is evidence. It is not the source of truth.

## Research basis

The design borrows narrowly from SLUMP/ProjectGuard, SpecPath, GitHub Spec Kit,
OpenSpec, ADR/MADR, and policy/state-machine practice:

- keep durable semantic project state outside a long agent conversation
- use stable requirement identities and explicit revision history
- preserve project principles and converge work against them
- represent accepted changes as explicit deltas
- keep accepted decisions immutable
- enforce deterministic transitions in code rather than in an LLM prompt

The full workflows of those systems are deliberately not copied.

## One authoritative source

Only the append-only event ledger is authoritative:

    .intent/events/*.yaml

Each accepted event contains the complete atom payload required to reconstruct
the contract.

Other files have narrower roles:

    .intent/approvals/
        principal confirmations bound to exact event hashes

    .intent/decisions/
        immutable ADR bodies referenced by SHA-256

    .intent/generated/active-contract.json
        disposable replay cache; never authoritative

    .intent/schema.json
        machine-readable event/atom shape

The earlier intent.yaml, contract.yaml, and changes/ authorities were removed
because they could diverge from replay.

## Event chain

Every event contains v, seq, prev, actor, ops, and note.

Rules:

- genesis is seq 0 with prev null
- later sequence numbers are contiguous
- prev must equal the previous canonical event hash
- replay checks the rule version on every event
- gaps, forks, malformed events, invalid transitions, missing required
  approvals, and decision-body hash mismatches fail replay
- tracked accepted history is append-only; preflight/append/converge reject
  working-tree changes and ordinary committed rewrites/deletions of accepted
  events, approvals, and decision bodies
- accepted-ledger mutation is serialized with a host-local file lock; the
  candidate base/head is rebuilt and revalidated inside that critical section

ledger_head identifies the exact audit history.

## Atom model

Binding fields are:

- id
- key
- kind: intent | invariant | requirement | decision
- authority: principal | agent
- scope.paths
- polarity: require | forbid
- statement
- verification: command | manual

Decision atoms additionally carry body_ref and body_hash.

id is a history handle. key identifies the semantic slot. Empty path scope is
global. Rationale/source/restoration provenance are non-binding audit metadata.

## Contract hash vs ledger head

contract_hash hashes normalized active binding semantics only:

- key
- kind
- authority
- normalized path scope
- polarity
- normalized statement
- verification
- decision body hash

IDs, rationale, provenance, and event history do not affect contract_hash.

Normalization uses Unicode NFC, collapsed statement whitespace,
sorted/deduplicated scope, canonical JSON, and deterministic atom ordering.

ledger_head hashes the canonical event chain. Equivalent final contracts may
therefore have the same contract_hash and different ledger_head values.

Intent Gate does not claim arbitrary natural-language paraphrases are equivalent.

## v0 operations

Only three operations exist.

### ADD

- new ID must never have been used
- exact semantic duplicates of active atoms are rejected as no-op history
- two different active atoms may not share a key with overlapping scope

### SUPERSEDE

v0 is deliberately strict:

- exactly one active old atom
- exactly one new atom
- new ID is unused
- key is unchanged
- normalized scope is unchanged

Split/merge and partial-scope replacement are deferred.

### CANCEL

- all targets must currently be active
- cancelling retires an obligation
- cancellation does not mean the opposite behavior is now forbidden

There is no RESTORE operation in v0. Reintroducing an old idea creates a new
atom and may carry non-binding restoration provenance.

A multi-op event is validated as one atomic state transition.

## Authority

Principal atoms protect project purpose and non-negotiable constraints.
Agent atoms cover automatically revisable engineering requirements.

An agent event cannot create, supersede, or cancel a principal atom.

A principal-authority event requires a separate approval bound to the exact
canonical event hash.

The CLI's trusted v0 channel is interactive confirmation:

1. build the exact candidate event
2. display its hash
3. require the principal to type APPROVE <hash>
4. enter the serialized append critical section
5. rebuild the event against the current ledger head and require its hash to
   equal the hash the principal approved
6. record version, hash, approver, and channel
7. append the event
8. replay and regenerate the cache

If the proposal or ledger changed after confirmation, the hash/base check
rejects the append and the principal must review the new candidate. Accepted
ledger mutation is serialized. Principal approval is published before the event,
so a crash cannot expose an authoritative principal event without its matching
approval; an orphan approval is inert. Atomic writes fsync file and directory
metadata before returning.

### Threat model

v0 prevents cooperative agent drift and accidental mutation. Event approvals
and manual-evidence approvals are bound to exact hashes, but their files still
live on the same machine. v0 therefore trusts the interactive principal channel
and ordinary Git history. It is not a security boundary against a malicious
process with arbitrary filesystem or Git-history rewrite access. Stronger
OS/cryptographic isolation is deferred until that threat model exists.

## Mechanical gate boundary

v0 enforces only facts it can determine mechanically:

- event-chain integrity
- authority
- legal transition
- ID freshness
- exact semantic duplicate
- key/scope collision
- path applicability
- active references
- verification metadata
- immutable decision-body hash
- generated-cache freshness
- convergence-evidence freshness

It does not silently use an LLM to decide:

- arbitrary prose contradiction
- paraphrase equivalence
- whether one natural-language request is broader than another
- whether source code semantically satisfies prose
- automatic conversation-to-contract acceptance

Semantic uncertainty is review input, not gate authority.

## Generated contract and brief

ctfh2 intent --root . resolve --write replays the ledger and writes the
non-authoritative materialized cache.

ctfh2 intent --root . brief --path <path> renders a compact brief stamped with
contract_hash and ledger_head.

The brief always carries active intent/invariants/principal commitments and adds
task-scope requirements/decisions by deterministic path overlap. No LLM relevance
ranking is used.

## Preflight

ctfh2 intent --root . check verifies:

- accepted history was not modified/deleted
- ledger replays successfully
- optional generated cache matches replay
- supplied atom references are active
- supplied path scope is compatible with those references

No supplied atom references produces a warning instead of guessing task meaning.

## Convergence

A convergence report is bound to contract_hash, ledger_head, git commit,
normalized path scope, and whether command verification was enabled. Evidence
created for another contract, history head, code commit, path scope, or command
mode is stale.

Convergence requires a Git worktree with a real HEAD commit and a clean
non-ignored worktree before verification. This ensures HEAD identifies the
exact implementation being judged; dirty source/config changes cannot reuse
evidence for the previous commit.

Verification commands are treated as observational. After they run, Intent Gate
checks worktree cleanliness, HEAD, contract_hash, and ledger_head again. A check
that mutates tracked implementation state cannot return PASS.

Decision atoms are integrity-checked during replay and are not separate
completion obligations.

For every applicable intent/invariant/requirement atom, PASS requires status
passed. Failed, deferred, open, not_run, and missing evidence all block.

A principal manual PASS requires principal-authored evidence plus a separate
principal approval bound to the canonical SHA-256 of that exact evidence
payload. The evidence binds contract_hash, ledger_head, commit, normalized path
scope, command mode, and the result set.

The approve-evidence CLI displays that evidence hash and requires an exact
interactive APPROVE-EVIDENCE confirmation. A text label such as actor=principal
alone cannot pass a principal obligation.

A deferred binding requirement is not done; if it should stop blocking, the
contract itself must be changed through an authorized event.

## Current CTFH genesis

The principal-approved genesis protects:

- maximize verified score by the event deadline
- authorized CTF/security scope only
- thin supervisor rather than a replacement generic agent framework
- independent authoritative result verification
- target lifecycle separated from disposable worker lifecycle
- replaceable model/agent/environment/browser/tool/context/skill components
- cost as guardrail rather than primary optimization objective
- evidence gate for added orchestration layers

The agent-revisable genesis requirements preserve:

- real native-agent vertical slice before deferred orchestration
- matched direct-native vs supervised A/B
- environment/tooling as part of the benchmarked profile
- planner/executor, pass@k, Jev, context-mode, codebase-memory, and learned
  routing deferred until paired evidence

The genesis also hashes the accepted Intent Gate ADR.

## CLI

    ctfh2 intent --root . resolve --write
    ctfh2 intent --root . brief --path src/ctfh_core
    ctfh2 intent --root . check --require-cache --ref INV-0001
    ctfh2 intent --root . append proposal.yaml --actor agent
    ctfh2 intent --root . append proposal.yaml --actor principal
    ctfh2 intent --root . approve-evidence evidence.yaml
    ctfh2 intent --root . converge --evidence evidence.yaml

Intent Gate runs at architecture/change/task boundaries, never for every CTF
shell/browser/debugger/model action.

## v0 test invariants

The implementation tests:

- full genesis replay
- approval hash tamper detection
- missing/malformed approval rejection
- principal approval bound to the exact candidate hash after confirmation
- principal authority protection
- add/cancel lifecycle
- stale proposal base rejection
- key/scope collision
- 1:1 same-key same-scope supersession
- partial-scope replacement rejection
- semantic path-invariance hash behavior
- fork/wrong-prev rejection
- immutable decision-body hash
- scope-aware brief rendering
- convergence binding to contract/head/commit
- convergence rejection on a dirty Git worktree
- principal evidence authority and exact evidence-hash approval
- deferred/open/not-run/failed blocking
- root-path and scoped convergence behavior
- absolute task-path rejection
- Unicode-normalized contract hashing
- strict schema/runtime event-shape parity
- generated-cache mismatch
- uncommitted and committed tracked-history mutation
- numeric event ordering
- rollback of failed event/approval publication
- real Git commit requirement
- verification-command mutation detection

## Deferred

Not part of v0:

- RESTORE or temporary overrides
- split/merge/partial-scope supersession
- branching/merging contract histories
- automatic natural-language contradiction or paraphrase detection
- automatic request-scope inference
- structural/symbol graphs
- full traceability graph
- codebase-memory integration
- separate strict/fast engines
- per-tool/per-turn gates
- distributed approval infrastructure
- OPA/Cedar/TLA+ runtime dependency
- automatic conversation-to-contract mutation

Any future addition must first demonstrate that this smaller mechanical gate is
insufficient.
