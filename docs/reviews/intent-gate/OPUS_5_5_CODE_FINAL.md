I reviewed the stdin snapshot by reading the code; I couldn't run the tests because shell commands needed approval. I also compared it with the working-tree `src/ctfh_core/intent_gate.py`, which already differs: it fixes B1 and B3 below. Everything else applies to both versions.

## BLOCKERS

**B1. Several changes in one event aren't checked against each other** (`_apply_event`; fixed in the working tree)
- References are checked against the state before the event, but changes are applied one by one.
- Two `ADD`s with the same new ID but different content both pass. So do `ADD X` plus `SUPERSEDE Y→X`. The first atom is silently overwritten and gets no status, which breaks "new ID must never have been used".
- Targeting the same atom twice (`CANCEL X` in two ops, or `SUPERSEDE X` plus `CANCEL X`) raises a bare `KeyError` at `new_active[x]`. Neither `preflight` (catches only `IntentGateError`) nor the CLI handler catches it, so the user gets a traceback.

**B2. The approval can be recorded for a different event than the one approved** (CLI `append` → `append_event`)
- The CLI computes the event hash, shows it, and waits for `APPROVE <H1>`.
- `append_event` then re-reads the proposal file and rebuilds the event as H2. It writes an approval for H2 labelled `channel: interactive-confirmation` and never compares H2 with H1.
- If the proposal file is edited while the prompt is waiting, the person has approved one event and a different one is recorded as approved.
- Fix: pass the confirmed hash into `append_event` and abort if it differs.

**B3. A crash during append can leave the ledger unreadable** (fixed in the working tree)
- The event file is written before its approval file. A crash or kill between the two leaves a principal event with no approval.
- After that, every `resolve`, `check`, and `append` fails until someone deletes the file by hand.
- Rollback uses `except Exception`, which doesn't catch Ctrl-C (`KeyboardInterrupt`).

**B4. The "accepted history can't change" check only runs in `check`**
- `converge` and `append` replay the ledger without calling `check_history_immutability`.
- Agent events need no approval, and nothing links forward to the last event. So deleting or rewriting a tracked event at the end of the chain still replays cleanly. For example, deleting the event that added a requirement makes `converge` return PASS without it.
- `append` will build on the shortened history. Once that is committed, `check` can't detect it either, because it only diffs against HEAD.

**B5. Evidence can go stale without being detected: a modified working tree is ignored**
- Evidence and reports are tied only to `git rev-parse HEAD`.
- If code changes are made after evidence was produced but not committed, that evidence still counts as current.
- Command checks run against the modified tree while the report claims the clean commit. This produces false PASS results.
- Fix: refuse to run, or record a tree hash, when there are uncommitted changes outside `.intent/generated`.

**B6. "Principal-authored evidence" is just a text label**
- `converge` accepts any evidence item that says `actor: principal`.
- Unlike events, there is no approval or hash binding.
- The gate's own error message ("requires principal-authored evidence") tells whoever writes the evidence that the fix is to set the label.
- All 8 principal non-decision atoms in the genesis (INT-0001, INV-0001..0007) are manual, so every real PASS depends on this label.
- This contradicts the doc's "Agent self-attestation cannot pass a principal obligation". Either bind principal evidence the way events are bound, or narrow the doc's claim.

**B7. A PASS report doesn't record how it was produced**
- The report omits the `--path` filter and whether `--no-command` was used.
- A PASS from a narrowed path set looks the same as a full PASS for the same contract hash, ledger head, and commit.
- So does a PASS where command checks were replaced by agent-supplied evidence (the agent-authority, `--no-command` branch).

**B8. `"."` and absolute paths match nothing instead of meaning "everything"**
- `_normalize_path_prefix(".")` (also `"/"` and `"./"`) returns `""`, and `_scope_overlap` treats `""` as matching nothing.
- `converge --path .` therefore skips every scoped agent requirement and returns PASS. `brief --path .` drops them too.
- An atom with `scope.paths: ["."]` is neither global nor matchable, and it escapes the key/scope collision check against scoped atoms with the same key.
- Absolute task paths also match nothing, silently.

**B9. The schema and the code disagree, and dropped fields aren't covered by the hash**
- The schema says `additionalProperties: false` for events and ops. The code silently drops unknown keys, at atom level too, and leaves them out of the event hash.
- So an approved event file can contain text its approval and the ledger head don't cover.
- The schema allows a decision atom without `body_ref`/`body_hash`, and those fields on non-decision atoms; the code rejects both.
- The schema requires an object-shaped `scope`. The code accepts a bare list, and treats a missing or `null` scope as global.
- The schema requires op type `"ADD"`; the code accepts lowercase.

**B10. The doc's list of tested behaviour claims more than the tests cover**
- **"1:1 supersession":** only rejection cases are tested. No test replays a successful SUPERSEDE and checks the `superseded` status.
- **"fork rejection":** only a wrong `prev` is tested, not two events with the same sequence number.
- **"semantic path-invariance hash":** the test only changes IDs, never how paths are spelled or ordered.
- **"deferred/not-run blocking":** only `deferred` is tested.
- **"rollback of half-written event/approval":** the test fails the cache write after both files are complete, so the half-written state is never exercised.
- Nothing tests `converge` with `--path`, command checks, `--no-command`, or duplicate IDs within one event.

## NON_BLOCKING
1. Append is still not fully crash-safe in the working tree. Rollback uses `except Exception` (misses Ctrl-C), files and the directory are not fsynced, and a rollback after the cache write leaves the cache pointing at a removed ledger head. `--require-cache` would detect that last case.
2. Loosely typed values get through, and some hash differently from their schema form:
   - `v: true` and `seq: true` pass because a bool is an int.
   - `note: null` and `rationale: null` become the string `"None"`.
   - A non-string `restores` is converted to text.
   - YAML dates inside `source` make `json.dumps` raise a `TypeError` that nothing catches.
3. Event files are sorted by name. From sequence 10000, `10000-…` sorts before `1001-…` and replay fails permanently. Approvals load only `*.yaml`, while events also accept `.yml`.
4. Approval channel, approver, and actor are unchecked labels:
   - Calling `append_event(confirmed=True)` directly records `interactive-confirmation` with no interaction.
   - `--actor principal` on agent-only changes stamps `actor: principal` with no prompt.
   - The genesis approval used `chat-confirmed-…`, not the documented interactive channel.
   - The doc should say these are labels, not enforcement.
5. Smaller order and environment issues:
   - The duplicate check runs op by op, but the collision check runs on the final state, so op order changes the outcome.
   - SUPERSEDE to an identical atom creates the no-op history that ADD forbids.
   - Decision bodies are no longer hash-checked once their atom is retired.
   - `converge` runs commands through `bash -lc` (login profile, hard-coded `/usr/bin/bash`) and captures hashes and commit before commands that may change the tree.

## VERDICT
NOT READY. The stdin snapshot fails on all ten blockers. The working tree fixes B1 and B3, but B2 and B4–B10 still block.
