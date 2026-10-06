**BLOCKERS**

**B1. There is more than one source of truth.**
- §15 lists four authorities: `intent.yaml`, `contract.yaml`, change events and decision records.
- §5 stores `status` and `supersedes` on the atom, but §7–8 derive both from events.
- The usual ADR/MADR habit is to edit the old record's status line, which breaks immutability.
- Any of these can disagree with replay. Editing `intent.yaml` or `contract.yaml` skips G1 entirely.

*Fix:*
- Make the accepted event log the only authority. A principal-approved genesis event creates the first principal atoms.
- Remove `status` and `supersedes` from the atom schema. Delete `intent.yaml` and `contract.yaml`, or make them generated views.
- Store decisions as atoms with `kind=decision`. The adding event references the ADR body by content hash. ADR files are never edited, and their status is derived from events.
- Drop `DECIDE` and `SUPERSEDE_DECISION`.

**B2. The state transitions are incomplete.**
- `SUPERSEDE(old_id, new_atom)` is one-to-one, but the tests need splits, and real use needs merges.
- `RESTORE` on a superseded atom leaves two active atoms for one slot.
- `RESTORE` on a cancelled atom skips re-checking against atoms added since.
- Nothing stops you from superseding or cancelling an atom that is already inactive, or from reusing an ID.
- Changes with several steps, such as swapping two conflicting atoms, are not atomic.

*Fix:* write a table of legal transitions.
- Keep three operations: `ADD`, `SUPERSEDE(old_ids[], new_atoms[])` and `CANCEL(ids[])`.
- Targets must be active, and new IDs must be unused.
- An event is an atomic batch, checked on the state after the whole batch.
- Drop `RESTORE`. To bring an atom back, `ADD` it again with a non-binding `restores:` note.

**B3. Replay is not well defined.**
- "In order" means filename order. Two branches can each add a `0005-*` event, each checked against a different base. A merge then silently produces a state no gate ever saw.
- Past events can be edited without detection.
- §8 checks contradictions and authority only on the final set, so an invalid history can still render a contract.

*Fix:*
- Each event carries `seq`, `prev` (hash of the previous canonical event) and `v` (rule version).
- The resolver folds events one by one. It checks each event against the state before it, using that event's rule version.
- It stops with an error on a gap, a fork, a hash mismatch or an invalid event.
- Authority is checked per event during replay, not on the final set.
- CI rejects changes to, or deletion of, existing event files.

**B4. The normal form is undefined, so the path-invariance test cannot work as written.**
Take two histories: `ADD R1(X); SUPERSEDE(R1→R2(Y))` and `ADD R1(Y)`. They describe the same contract but differ in id, created_at, source, rationale and lineage. If any of those enter the hash, the §18 test fails. If it is left unspecified, the test cannot be run.

*Fix:*
- Binding fields are `{key, kind, authority, scope, polarity, statement, verification, body_hash}`.
- Canonicalize: Unicode NFC text, collapsed whitespace, sorted and deduplicated scope, canonical JSON, and ordering by (kind, key, content hash).
- `contract_hash` covers binding fields only.
- `ledger_head`, the chain hash, is the separate audit identity.
- IDs are handles, not contract content.

**B5. Authority rules have gaps.**
G1 only asks whether a change "modifies a principal_only atom". These cases are not covered:
- The agent adds a principal_only atom.
- A principal atom is superseded by an agent-tier atom, which quietly downgrades it.
- A principal atom is cancelled or restored.
- An agent atom narrows or contradicts a principal atom.
- At C1, the agent defers or waives a principal requirement, or records a manual pass for one itself.
- The agent picks fast or strict mode.

*Fix:*
- An event's required authority is the highest authority of any atom it changes or creates, including any change of tier.
- Events written by the agent may only create, supersede or cancel agent-tier atoms.
- A conflict between an agent atom and a principal atom is rejected outright, not turned into a confirmation prompt.
- Deferring, waiving or manually verifying a principal atom needs the principal.
- Document the remaining gap: if the agent records a user's statement at the wrong tier, the gate cannot detect it.

**B6. "Explicit user confirmation" has no mechanism.**
The agent writes the event files, so `approved: true` is just the agent vouching for itself. The §18 authority test then relies on trust.

*Fix:*
- An approval is a separate record `{event_hash, approver}`, produced only through a channel the principal controls, such as an interactive confirm command or a commit signed by the principal.
- The resolver needs a matching approval for every event that requires principal authority.
- The approval covers the exact event bytes, so any later edit invalidates it.
- v0 may document trust in the channel itself, but binding the approval to the event hash is required.

**B7. The gates are said to need no LLM, but several need judgment about meaning.**
- G2 (general contradiction), G3 (scope "broader than the request"), G4 ("planned change") and G5.
- §4's "still satisfies every principal atom" and §14's "inside existing principal intent".
- Detecting restatements, plus C4 and C6.

Left unspecified, these will either quietly use an LLM or do nothing.

*Fix:* draw a hard line in v0. The gate enforces only:
- A required `key` field, and at most one active atom per (key, overlapping scope). A conflicting `ADD` must be a `SUPERSEDE` instead, with no tie-breaking by recency or specificity.
- Binding content identical to an existing atom is a no-op.
- Traceability means atom IDs in commit trailers or test annotations, and those IDs must be active.

Anything that needs judgment about meaning is advice from the proposer, never gate authority. Contradictions between atoms with different keys surface only through invariant checks at convergence. Document that limit.

**B8. Convergence results are not tied to a state.**
- A PASS computed against contract N or commit A gets reused after the ledger reaches N+1 or the code changes.
- "Explicitly deferred" has no defined effect on PASS.
- §19's "frozen contract" does not say what happens when the ledger moves on during work.

*Fix:*
- The convergence report records `{contract_hash, ledger_head, commit}` and is stale if any of them changes.
- PASS means: all invariants pass, the authoritative acceptance check passes, every principal requirement passes, and each agent requirement passes or is listed as debt. Anything else is BLOCKED.
- Work started on an older `contract_hash` reruns preflight against the current head before convergence.

**MINIMAL_V0**

- **Layout:**
  - `.intent/events/NNNN-slug.yaml`: the only authority. Genesis event `0000` holds the principal atoms and is approved.
  - `.intent/approvals/`: produced by the principal, bound to event hashes.
  - `.intent/decisions/*.md`: immutable bodies, referenced by hash.
  - `.intent/generated/active-contract.json`: a cache stamped with `contract_hash` and `ledger_head`, never read as authority.
  - `.intent/schema.json`.
- **Atom:**
  - Binding fields: `id`, `key`, `kind{intent, invariant, requirement, decision}`, `authority{principal, agent}`, `scope.paths` (path prefixes; empty means global), `polarity{require, forbid}`, `statement`, `verification{command|manual, ref}`, `body_hash` (decisions only).
  - Non-binding fields: `rationale`, `source`.
- **Event:** `v`, `seq`, `prev`, `actor`, `ops[ADD | SUPERSEDE(n:m) | CANCEL]`, `note`.
- **Resolver:** folds events and checks each one for unused IDs, active targets, authority, approval, key/scope uniqueness and a verifier being present. It outputs the active set, `contract_hash` and `ledger_head`, byte-identical on every run.
- **Commands:** `append` (validate and write), `approve`, `resolve`, `brief`, `check` (preflight), `converge`.
- **Brief:** built by fixed rules. It always includes every active intent, principal and invariant atom. It adds requirements and decisions whose scope overlaps the task's paths, and is stamped with `contract_hash`. No relevance ranking.
- **Preflight:** replay is valid; the cached contract matches the head; every referenced ID is active; every change that needs the principal is approved.
- **Convergence:**
  - Run the command verifiers for active invariants, requirements and acceptance.
  - Record manual results; the principal records them for principal atoms.
  - A reference to an inactive ID is an error (the mechanical part of C4).
  - An untraced commit is a warning.
  - The report is bound to state as in B8.
- **One policy:** the current fast-mode stop conditions, minus G3.
- **Tests:** the §18 list, plus:
  - an edited past event fails replay;
  - a fork (duplicate `seq` or `prev`) fails;
  - a history with an invalid event fails;
  - a downgrade through supersede is rejected;
  - an agent cancelling a principal atom is rejected;
  - an approval with the wrong hash is rejected;
  - a key collision forces a supersede;
  - an edited decision body is detected;
  - a stale convergence report is rejected;
  - an agent deferring a principal requirement is rejected;
  - two equivalent histories with different IDs give the same `contract_hash` but different `ledger_head`.
- **Relocation:** move project-specific content (the §4 examples, §6, §19, §20) out of the generic design and into the genesis event.

**DEFER**

- Strict mode and choosing between modes.
- G3, C6, contradiction or restatement detection that needs judgment, and C4 beyond ID references. If wanted later, these can be an optional advisory reviewer outside the gate.
- `structure.json`, `traceability.json`, the traceability graph, atom-to-code mapping and codebase index integration.
- Scope by modules or profiles, and glob matching.
- The `prefer` polarity.
- The `RESTORE`, `DECIDE` and `SUPERSEDE_DECISION` operations, and the `acceptance` and `decision_ref` kinds.
- Verification kinds beyond `command` and `manual`.
- C5 checks on watched paths beyond ID references.
- References between atoms and orphan detection.
- A log of rejected proposals, and snapshots or compaction.
- Cryptographic signing beyond hash-bound approvals.
- OPA, Cedar and TLA+.

**VERDICT**

NOT READY. The direction is right and the v0 scope is close. The blockers are gaps in the spec, not missing features, and fixing them makes the system smaller: two fewer authoritative files, three operations instead of six, four kinds instead of five, two polarities instead of three, and one mode instead of two. It is READY once B1–B8 are written into the design and the tests.
