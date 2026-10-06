BLOCKERS

- **Replay cannot reconstruct an authoritative contract.** The [initial events](/home/yc54616/Desktop/ctfh-core/.intent/changes/0001-initial-contract.yaml:8) contain IDs without atom payloads; replay therefore depends on the mutable snapshot. Meanwhile, the design declares four artifacts authoritative without reconciliation rules. Choose immutable genesis plus complete accepted deltas as the source, and require snapshots to match replay. Define how every `intent.yaml` commitment and active ADR enters the normalized contract; several non-negotiables currently have no corresponding atom.

- **`principal_only` lacks an enforcement boundary.** [G1](/home/yc54616/Desktop/ctfh-core/docs/INTENT_GATE.md:321) requires confirmation but defines no trusted confirmation input. `source.type: user` and `status: accepted` cannot establish approval by themselves. Bind approval to the exact delta and base revision. Check additions, cancellation, restoration, supersession, and changes to authority, scope, or verification against the existing protected state. Explicitly state whether enforcement assumes cooperative agents or a protected write boundary.

- **The transition relation is incomplete.** [`RESTORE`](/home/yc54616/Desktop/ctfh-core/docs/INTENT_GATE.md:293) could reactivate a superseded atom alongside its replacement. Define permitted predecessor states, fresh-ID requirements, invalid-reference handling, and whole-change atomicity. The smallest v0 can omit restoration. Cancellation removes an obligation; it does not automatically prohibit the former behavior.

- **Path invariance has no defined equivalence or normalization.** The [required hash test](/home/yc54616/Desktop/ctfh-core/docs/INTENT_GATE.md:608) leaves ordering, defaults, provenance, timestamps, supersession ancestry, and differing IDs unspecified. Define a canonical projection and whether equivalence requires matching stable IDs. Limit restatement detection to declared identity or exact normalized equality; arbitrary prose equivalence cannot be assumed.

- **The gates promise semantic enforcement without executable inputs.** [G2–G4](/home/yc54616/Desktop/ctfh-core/docs/INTENT_GATE.md:328) cannot determine prose contradictions, request scope, or genuine traceability from the current schema. A link to a broad objective proves little. Define bounded mechanical checks over explicit conflicts, scope, and mappings, with unresolved semantic questions requiring review. Resolve the inconsistency between G3 allowing scope expansion through “justification” and fast mode stopping on broadened scope.

- **Convergence has no complete pass condition.** [C1](/home/yc54616/Desktop/ctfh-core/docs/INTENT_GATE.md:447) permits classification as satisfied, deferred, or failed, while fast mode lists only failed invariants and acceptance as completion blockers. Specify that failed, missing, unreviewed, or unexecuted mandatory checks prevent success. Deferral needs an authorized scope change, not an agent-selected completion label. Bind verification results to the checked contract and implementation revision; a verification file’s existence is insufficient.

MINIMAL_V0

- **One schema and authority model:** immutable atoms, explicit defaults, mandatory versus preferred obligations, scoped verification definitions, and trusted approval inputs. Include all existing principal commitments.
- **One deterministic reducer:** complete genesis; ordered, append-only `ADD`, `SUPERSEDE`, and `CANCEL` transactions; immutable ADR references and supersession links; canonical active projection, hash, and compact brief.
- **One controlled apply operation:** validate the base revision, authority, references, and resulting contract before accepting the entire delta. Detect modified/deleted accepted history against a trusted prior revision. Treat `contract.yaml` as a checked materialization.
- **One preflight and one convergence check:** validate explicit conflicts, task scope, active-atom mappings, required verification, and revision-bound results. Use simple mappings and recorded review outcomes. Report unresolved obligations explicitly.
- **Focused tests:** legal/illegal transitions, unauthorized protected changes, altered history and snapshot mismatch, equivalent-history normalization, inactive references, missing verification, and stale or failing evidence.

This is small enough for fast engineering when invoked at contract changes and task completion. Ordinary edits within an existing task scope should require neither new atoms nor proposal ceremonies.

DEFER

- `RESTORE`, temporary overrides, concurrent ledger writers, and distributed approval infrastructure.
- Automatic natural-language contradiction, paraphrase, or scope inference; automatic deletion of code associated with inactive atoms.
- Structural indexes, full traceability graphs, mandatory strict-mode reviews, dashboards, and additional workflow stages. These would make v0 unnecessarily heavy.

VERDICT

NOT READY