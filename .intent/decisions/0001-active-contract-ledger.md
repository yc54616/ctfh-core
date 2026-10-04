# ADR-0001 — Use Active Contract Ledger as the Intent Gate Core

Status: accepted
Date: 2026-10-05

## Context

Long-running coding agents lose fidelity when requirements emerge incrementally.
Conversation history is append-only while the binding specification is mutable.
Recent work on SLUMP/ProjectGuard and SpecPath suggests explicit external
specification state and stable requirement identities are useful mitigations.

## Decision

CTFH will use a lightweight active-contract ledger:

- stable atom IDs
- explicit active/superseded/cancelled states
- principal-only vs agent-revisable authority
- append-only accepted change events
- immutable/superseding decision records
- deterministic preflight and convergence gates

The ledger is authoritative for project intent. Conversation history is evidence,
not the runtime source of truth.

## Consequences

Positive:
- reduces silent requirement drift
- makes overrides/cancellations explicit
- supports compact worker briefs
- provides traceability for future agents

Negative:
- introduces a small amount of project-state maintenance
- requires disciplined supersession instead of rewriting history

## Rejected alternatives

### Full Spec Kit workflow for every change

Rejected because its full multi-stage workflow is too heavy for small/frequent
CTFH changes.

### OpenSpec proposal directory for every edit

Rejected as mandatory workflow. Delta semantics and audit history are retained.

### LLM-generated summary as source of truth

Rejected because summaries can preserve ambiguity and recency bias.

### OPA/TLA+ as v0 dependency

Rejected as excessive for the first implementation. We keep deterministic rules
and state-machine/invariant thinking without those runtime dependencies.
