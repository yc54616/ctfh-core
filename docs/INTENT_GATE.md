# Intent Gate — Active Contract Architecture

Status: design draft before implementation
Date: 2026-10-05

## 1. Problem

A long-running coding/agent project does not fail only because context is too
small. It also fails because the current binding specification becomes
ambiguous as requests accumulate.

Conversation history is append-only. Project intent is mutable.

A later message may:
- add a requirement
- narrow its scope
- replace it
- temporarily override it
- cancel it
- propose an alternative that was never accepted

"Remember everything" is therefore wrong.
"Follow the latest message" is also wrong.

The system must resolve an **active contract** before implementation.

## 2. Research basis

### SLUMP / ProjectGuard

"When the Specification Emerges" (arXiv:2603.17104) measures faithfulness loss
when specifications are progressively disclosed over long coding sessions.

Findings:
- single-shot specs were more faithful on most evaluated tasks
- structural integration degraded on both Claude Code and Codex
- ProjectGuard, an external semantic + structural project-state layer,
  recovered 90% of Claude Code's measured faithfulness gap in the paper

ProjectGuard separates:
- semantic state: durable accepted design commitments
- structural state: repository layout, public symbols, interface relationships

Decision:
- Intent Gate needs durable semantic state outside the agent conversation
- repository structure is a separate generated view, not the source of intent

### SpecPath

SpecPath (arXiv:2608.09799) identifies specification-path sensitivity:
contract-equivalent requirement histories can produce different implementations.

Important abstraction:
- requirements are stable atoms
- atoms have scope and polarity
- revision events activate/deactivate atoms
- final behavior derives from the active set, not from recency

The paper suggests an explicit contract ledger with requirement identities and
statuses such as active, superseded, or cancelled.

Decision:
- requirements get stable IDs
- overrides/supersession/cancellation are explicit state transitions
- implementation plans derive from the active subset only

### GitHub Spec Kit

Useful ideas:
- constitution: persistent project principles
- specify -> plan -> tasks -> implement
- read-only cross-artifact analyze
- converge implementation back against spec/plan/tasks

Decision:
- keep a small constitution/invariant layer
- provide preflight consistency and post-implementation convergence gates
- do not copy the full multi-command workflow into the hot path

### OpenSpec

Useful ideas:
- change proposals are separate from canonical specs
- delta specs explicitly ADDED / MODIFIED / REMOVED / RENAMED
- validation before archive
- completed changes become an auditable history

Decision:
- changes are deltas against the active contract
- accepted changes are folded into canonical state
- history is append-only/auditable

### ADR / MADR

Useful ideas:
- accepted decisions are not silently edited
- a changed decision is superseded by a new decision
- rationale, alternatives, consequences and reevaluation triggers are preserved

Decision:
- architectural choices live in append-only decision records
- active contract references active decisions

### OPA / Cedar / policy-as-code

Useful ideas:
- rules are declarative
- inputs and decisions are separated
- policy evaluation can explain why a request was allowed/denied

Decision:
- gate rules are deterministic and explainable
- do not embed gate authority inside an LLM prompt

### State-machine/property methods

TLA+/PlusCal and property-based testing reinforce a useful split:
- state transitions describe what may happen
- invariants describe what must always remain true

Decision:
- Intent Gate is a small state machine with executable invariants
- full formal verification is not required for v0

## 3. Design thesis

Intent Gate is **not** another spec-driven development framework.

It is a small active-contract resolver placed before planning and after
implementation.

```
new request / conversation turn
            │
            ▼
      Intent Resolver
            │
            ▼
   Contract Delta Proposal
            │
            ▼
       Change Gate
            │
     ┌──────┼────────┐
     ▼      ▼        ▼
   allow  confirm   reject
     │
     ▼
 Active Contract Ledger
     │
     ├─ principal intent
     ├─ invariants
     ├─ requirements
     ├─ decisions
     └─ acceptance
     │
     ▼
 plan / implementation
     │
     ▼
 Convergence Gate
     │
     ├─ traceability
     ├─ invariant checks
     ├─ acceptance checks
     └─ decision consistency
```

The gate resolves **what is binding**.
It does not decide **how to implement it**.

## 4. Authority tiers

### principal_only

May change only with explicit principal/user confirmation.

Examples:
- project purpose
- optimization objective
- non-negotiable constraints
- safety/authorization boundaries
- success criteria
- hard exclusions

For CTFH:
- performance-first
- verified result by deadline
- native-agent thin-supervisor principle
- no hidden generic ReAct replacement
- authorized CTF scope only

### agent_revisable

The agent may revise these without asking if the new value still satisfies every
active principal-only atom.

Examples:
- current architecture
- implementation strategy
- module selection
- benchmark plan
- internal APIs
- rollout order

All revisions are recorded.

### derived

Disposable/regenerable.

Examples:
- task lists
- estimates
- temporary hypotheses
- short-term plan
- work queue

Derived data is never allowed to override the active contract.

## 5. Contract atom

Every binding item has a stable identity.

Conceptual schema:

```yaml
id: REQ-0042
kind: intent | invariant | requirement | acceptance | decision_ref
authority: principal_only | agent_revisable
scope:
  paths: []
  modules: []
  profiles: []
polarity: require | forbid | prefer
status: active | superseded | cancelled
statement: "..."
rationale: "..."
source:
  type: user | decision | research | derived
  ref: "..."
supersedes: []
verification:
  kind: test | command | review | metric | manual
  ref: "..."
created_at: ...
```

Rules:
- IDs never change
- text may not be silently rewritten
- semantic changes create a new atom and supersede the old one
- cancelled atoms remain in history
- only active atoms enter the implementation brief

## 6. Principal intent record

The project has a very small top-level intent record.

For CTFH the initial principal intent is:

```
Purpose:
Build a competition CTF supervisor that maximizes verified score by the event
deadline.

Primary success:
Higher verified solve rate / score-by-deadline than the direct native-agent
baseline.

Non-negotiables:
- authorized CTF scope
- native agents remain native
- every added layer must earn its place in paired evaluation
- outcome verification is independent from worker self-report
- model/tool/browser/context components remain replaceable over time
- cost is a guardrail, not the main optimizer
```

This record should stay short enough to inject on every important planning turn.

## 7. Change events

Conversation turns are converted into explicit events, not directly into spec
text.

Event types:

```
ADD(atom)
SUPERSEDE(old_id, new_atom)
CANCEL(atom_id)
RESTORE(atom_id)
DECIDE(decision_id)
SUPERSEDE_DECISION(old_id, new_id)
```

A restatement/no-op produces no state change.

This makes "actually changed the contract" distinct from "talked about an idea."

## 8. Active-contract resolution

Resolution is deterministic:

1. replay accepted change events in order
2. resolve supersession/cancellation
3. select active atoms
4. check contradictions
5. check required authority
6. render normalized active contract

The normalized contract is the only binding input to planning.

Conversation history remains audit evidence, not the runtime source of truth.

## 9. Change Gate

Before a contract delta is accepted, evaluate:

### G1 — authority

Does this change modify a principal_only atom?

- no -> may proceed automatically
- yes -> explicit user confirmation required

### G2 — contradiction

Does the delta conflict with an active invariant/requirement?

- no -> continue
- yes -> reject or explicitly supersede the conflicting atom

No implicit conflict resolution.

### G3 — scope

Is the proposed scope broader than the request?

If yes, require justification or confirmation.

### G4 — traceability

Can every planned change point to at least one active atom?

Untraced work is rejected as scope drift.

### G5 — decision consistency

Does this invalidate an accepted architectural decision?

If yes, create a superseding decision instead of silently editing history.

### G6 — verification

Does each binding requirement have a verification mode?

Implementation may begin with a manual/review verifier, but an unverified
requirement is visible debt rather than silently "done."

## 10. Intent brief

Workers should not receive the full ledger every turn.

Render a compact brief:

```
INTENT
- purpose
- primary objective

MUST PRESERVE
- relevant principal-only atoms
- relevant invariants

ACTIVE REQUIREMENTS
- only atoms relevant to this task

ACTIVE DECISIONS
- relevant decision summaries

ACCEPTANCE
- how this task will be judged

OUT OF SCOPE
- relevant forbidden/non-goal items
```

This is ProjectGuard-style compatibility-aware injection without replacing the
native agent's context system.

## 11. Structural state

Maintain a generated, non-authoritative structural view:

- files/modules
- public symbols
- important interfaces
- atom -> code/test mappings
- decision -> affected paths

Possible later implementation:
- direct AST/index
- codebase-memory
- simple ripgrep/static scan

The structural view answers "where does this commitment live?"

It may be regenerated at any time.
It cannot modify the semantic contract.

## 12. Traceability graph

Minimal relations:

```
Intent
  -> Requirement
Requirement
  -> Acceptance
Requirement
  -> Decision
Requirement
  -> Code paths
Requirement
  -> Tests
Decision
  -> Code paths
Change
  -> Atoms changed
```

The goal is not perfect documentation.

The goal is to detect:
- requirement with no implementation
- implementation with no requirement
- superseded requirement still controlling code
- decision changed without supersession
- test no longer covering its binding requirement

## 13. Convergence Gate

After implementation:

### C1 — active contract coverage

Every active requirement is:
- satisfied
- explicitly deferred
- or failed

No silent missing item.

### C2 — invariant preservation

Run executable invariant/property tests.

For non-executable invariants, require explicit review result.

### C3 — acceptance

Run the authoritative acceptance command/test/benchmark.

### C4 — stale-contract removal

Check whether code/tests still encode superseded/cancelled atoms.

### C5 — decision consistency

Changed watched architecture areas must either:
- comply with the active ADR
- or reference a superseding ADR

### C6 — unexpected behavior

Record new behavior not traceable to an active atom.

This is a warning/gate depending on severity.

## 14. Modes

Intent Gate should not slow CTF work.

### fast mode — default

Automatic:
- ADD agent_revisable requirement
- derived plan/task regeneration
- implementation detail changes
- decision creation inside existing principal intent

Stops only on:
- principal-only change
- direct contradiction
- broadened authority/scope
- failed invariant
- failed authoritative acceptance

### strict mode

For architecture migrations / release / benchmark changes:
- every contract change rendered for review
- full traceability required
- convergence gate blocks completion on warnings

## 15. Repository layout

Proposed:

```
.intent/
├── intent.yaml
├── contract.yaml
├── changes/
│   └── 0001-*.yaml
├── decisions/
│   └── 0001-*.md
├── generated/
│   ├── active-contract.json
│   ├── structure.json
│   └── traceability.json
└── schemas/
    └── contract.schema.json

docs/
└── INTENT_GATE.md
```

Authoritative:
- intent.yaml
- contract.yaml
- accepted change events
- accepted/superseded decision records

Generated/cache:
- active-contract.json
- structure.json
- traceability.json

## 16. What not to copy from other systems

Do not reproduce the full Spec Kit pipeline inside CTFH.

Do not require:
- specify
- clarify
- plan
- checklist
- tasks
- analyze
- implement
- converge

for every small change.

Do not make OpenSpec proposal folders mandatory for every edit.

Do not make an LLM summary the source of truth.

Do not let recent conversation text silently override an older binding rule.

Do not use a giant OPA/TLA stack in v0.

Take only:
- stable contract IDs/statuses
- delta semantics
- immutable decision history
- deterministic gate rules
- pre/post convergence checks

## 17. v0 Intent Gate

Build only:

1. intent.yaml
2. contract.yaml with stable atoms
3. append-only change events
4. deterministic resolver
5. preflight gate
6. compact intent brief renderer
7. convergence checks for:
   - active requirements
   - invariants
   - acceptance commands
8. ADR supersession convention

No LLM is required inside the gate.

The native agent may propose a delta, but the gate resolves and enforces it.

## 18. Required tests

### active contract

- add requirement -> active
- supersede -> old inactive/new active
- cancel -> inactive
- split additions -> both active
- duplicate/restatement -> no semantic change

### authority

- agent cannot change principal_only atom
- principal confirmation can

### path invariance

Build multiple change histories resolving to the same final contract.
The normalized active contract hash must be identical.

### traceability

- active requirement without acceptance -> visible error
- implementation change with no atom -> drift warning/error
- superseded requirement referenced by new plan -> reject

### decisions

- accepted decision cannot be edited in place
- superseding decision links old/new records

### convergence

- failed invariant blocks completion
- failed acceptance blocks completion
- cancelled requirement is not required for completion

## 19. Integration with CTFH

Intent Gate sits outside the solver hot loop.

```
project request/change
      -> Intent Gate
      -> frozen active contract
      -> implementation / benchmark work

live CTF event
      -> CTFH Kernel
      -> solver workers
```

Do not run Intent Gate for every shell/browser/tool action.

Use it when:
- changing CTFH architecture
- changing solver strategy defaults
- adding/removing modules
- changing optimization objective
- changing benchmark/promotion rules
- changing safety/authorization boundaries

This preserves project direction without adding latency to competition actions.

## 20. Current CTFH gate target

The first gate should protect these already-accepted commitments:

- performance-first
- thin supervisor, not another generic agent framework
- native-agent baseline first
- real vertical slice before deferred modules
- environment/tooling is part of the benchmarked profile
- outcome authority independent from worker self-report
- target lifecycle separate from worker lifecycle
- matched A/B required for wrapper-overhead claims
- modules only promoted after paired benchmark evidence
- planner/executor, pass@k, Jev, context-mode, codebase-memory and learned routing
  remain deferred until their own evidence gate passes
