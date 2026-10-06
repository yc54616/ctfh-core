BLOCKERS

- **Replay has no authoritative source.** The [initial change record](/home/yc54616/Desktop/ctfh-core/.intent/changes/0001-initial-contract.yaml) lists atom IDs without their contents, so it cannot reconstruct the [canonical contract](/home/yc54616/Desktop/ctfh-core/.intent/contract.yaml). The design also calls the intent file, contract, and events authoritative without defining how disagreements are resolved.
- **`principal_only` is not enforced yet.** `status: confirmed` and a free-text user source do not prove approval of a specific change. The gate needs a trusted confirmation bound to the exact delta, and it must cover direct edits to principal intent as well as atom events.
- **State and hash rules are underspecified.** Define valid `ADD`, `SUPERSEDE`, `CANCEL`, and `RESTORE` transitions, especially whether a superseded atom can return. Define which binding fields enter the normalized hash and their ordering. Semantic paraphrases cannot be treated as deterministically identical.
- **The proposed gates can produce false passes.** Free-text contradiction checks and scope comparison are not deterministic, while current atoms omit scope. At closeout, a documentation reference is not verification evidence, and “explicitly deferred” must not count as satisfied without an authorized exception. Define the mechanical checks and the outcome for cases requiring review.

MINIMAL_V0

- One replayable accepted event log containing full atom payloads; validate `intent.yaml` and `contract.yaml` as projections against it.
- One small resolver with unique IDs, explicit transition rules, canonical serialization, and an active-contract hash.
- One preflight command checking authority, declared scope, active-atom references, and machine-detectable conflicts; route unresolved semantic conflicts to review.
- A compact brief renderer containing only active, relevant commitments.
- A closeout command recording actual evidence for affected requirements and global invariants, with missing, failed, and deferred outcomes kept distinct.
- Focused tests for replay, projection mismatch, transitions, approval binding, normalization, and failed closeout.

DEFER

- Structural indexing, a traceability graph, generated caches, and strict mode.
- LLM semantic deduplication or automatic natural-language contradiction judgments.
- Per-tool-action gating and a new planning workflow.

VERDICT

- **NOT READY**