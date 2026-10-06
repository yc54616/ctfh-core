BLOCKERS

- **Replay cannot establish a single truth.** The [initial change](/home/yc54616/Desktop/ctfh-core/.intent/changes/0001-initial-contract.yaml:8) contains IDs without atom payloads, while both events and `contract.yaml` are authoritative. Editing canonical text would change replay without an event. Choose immutable, complete accepted events plus a checked canonical projection. Also preserve every principal constraint from `intent.yaml`; authorized scope, replaceability, and the cost guardrail currently lack corresponding atoms.

- **Principal authority has no enforcement boundary.** `status: accepted` and `source.type: user` are assertions an agent can write. Specify a trusted acceptance path binding confirmation to the exact delta and base revision. Check authority against each event’s **pre-transition state**, including cancellation, restoration, supersession, authority downgrades, scope changes, and weakened verification. Checking only the final active subset can miss removed protected obligations. ([Authority rules](/home/yc54616/Desktop/ctfh-core/docs/INTENT_GATE.md:172))

- **RESTORE and scoped supersession are ambiguous.** Restoring a superseded atom could activate both competing versions. Replacing a broadly scoped atom with a narrower one could silently discard obligations outside the new scope. Define permitted transitions and reject ambiguous operations; omitting restoration and temporary overrides from v0 is sufficient. ([Events](/home/yc54616/Desktop/ctfh-core/docs/INTENT_GATE.md:282))

- **The gates exceed their available inputs.** Arbitrary prose does not support deterministic contradiction or compatibility checking. Linking any change to the broad performance objective does not establish scope or traceability. G3 also permits broadened scope through mere “justification.” Define bounded mechanical checks, explicit request scope, and review-required semantic unknowns. Agent-written justification must not authorize scope expansion. ([Change gates](/home/yc54616/Desktop/ctfh-core/docs/INTENT_GATE.md:317))

- **Convergence lacks an explicit success predicate.** Listing requirements as satisfied/deferred/failed is accounting, not completion. Define task completion versus whole-contract completion. An applicable required obligation that is failed, deferred, unverified, or not run cannot count as satisfied. Verification references need actual results tied to the frozen contract and implementation snapshot; a README path alone supplies no evidence. ([Convergence](/home/yc54616/Desktop/ctfh-core/docs/INTENT_GATE.md:443))

MINIMAL_V0

- **One schema and reducer:** immutable atom definitions; explicit global/scoped applicability; `ADD`, `SUPERSEDE`, and `CANCEL`; validated references and transitions. Cancellation retires an obligation; it does not automatically prohibit its behavior or reactivate its predecessor.

- **One serial acceptance operation:** validate the complete delta against the current revision, enforce the trusted principal boundary, and append the accepted transaction. Record acceptance/supersession of immutable ADR references through the same history.

- **One resolver and canonical consistency check:** reconstruct from immutable genesis plus accepted events. Treat `contract.yaml` as a checked materialization. Reject disagreement, duplicate identities, malformed events, and unauthorized history changes.

- **One normalized representation:** define equivalence explicitly, including atom-ID treatment. Canonicalize ordering and scope sets; exclude audit-only timestamps, provenance, and event order from the semantic hash. Guarantee equality for declared contract equivalence, without claiming automatic equivalence of arbitrary prose.

- **One preflight and compact brief:** check authority, explicit conflicts, declared work scope, active supporting IDs, decision references, and verification specifications. Include applicable principal constraints. Use flat mappings; semantic uncertainty remains an explicit review result.

- **One convergence report:** evaluate applicable obligations against a frozen contract/code snapshot, with authoritative evidence and explicit satisfied/failed/open/not-run states. Retired obligations stop controlling completion; shared code remains permissible when supported by active obligations.

- **Focused invariant tests:** replay reconstruction and tamper detection; protected cancellation/downgrade rejection; supersession/cancellation transitions; equivalent-history normalization; scope and stale-reference rejection; failed or missing evidence preventing completion. Run the layer at contract changes and engineering task boundaries, outside the solver loop.

DEFER

- RESTORE, temporary overrides, partial-scope replacement, and branching or merging contract histories.
- Automatic prose contradiction detection, semantic equivalence, implementation-compliance inference, and automatic conversation-to-contract acceptance.
- Structural indexes, symbol graphs, graph databases, dashboards, elaborate policy engines, formal verification, and separate fast/strict implementations.
- Mandatory proposal folders, full workflow stages, and per-tool/per-turn gates. The proposed boundary placement is lightweight; those additions would undermine fast engineering.

VERDICT

NOT READY