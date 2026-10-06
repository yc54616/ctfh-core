Bash was denied in this session, so I couldn't run the test suite, any git commands, the genesis replay, or the repro below. Everything here comes from reading the code.

**BLOCKERS**

**B1 — Unreplayable accepted state: decision `body_ref` can point anywhere in the project, and an agent can lock the whole ledger with it.**
- `normalize_atom` only does NFC and strip on `body_ref` (`src/ctfh_core/intent_gate.py:301`). `_decision_body_ok` (`intent_gate.py:446-462`) only checks that the path resolves inside the project. The schema has no pattern for it either (`.intent/schema.json:111`).
- The docs say decision bodies live in `.intent/decisions/` (`docs/INTENT_GATE.md:45-46`), and that is the only directory `check_history_immutability` protects.
- During replay, `_apply_event` re-hashes the body of every decision that was active after each historical event (`intent_gate.py:731-733`). `build_candidate_event` calls `resolve()` before anything else (`intent_gate.py:866`). So once a body changes or disappears, nothing more can be appended, not even a CANCEL of that decision.
- Repro (agent authority only, normal CLI):
  1. ADD an agent decision with `body_ref: .ctfh-runs/adr.md` (that directory is gitignored) and the correct sha256.
  2. Run `append --actor agent`; it succeeds. Commit the event. Converge's clean-worktree check passes because the body is ignored.
  3. On a fresh clone, or after `rm -rf .ctfh-runs`, `resolve`, `check`, `append` and `converge` all fail with "missing decision body". There's no way to recover through the protocol.
- The same lockout happens if a decision references a tracked doc such as `docs/ARCHITECTURE.md` and someone later edits it normally.
- Minimal fix:
  - Normalize `body_ref` with `_normalize_path_prefix` and require the `.intent/decisions/` prefix.
  - In `_decision_body_ok`, confirm the resolved path stays under `(root/.intent/decisions).resolve()`, so symlinks can't escape.
  - Add a regression test for this case.
  - Genesis DEC-0001 already uses that directory, so no hashes change.

**NON_BLOCKING**
1. Accepted events and approvals aren't protected until they're committed. A `git clean -fd` or `git stash -u` silently rolls back approved principal changes. The genesis event and its approval are untracked right now, so commit them.
2. The approval prompts show only the hash, not the event or evidence content (`cli.py:132-136`, `cli.py:159-163`). The principal is effectively approving blind.
3. `_normalize_path_prefix` gives a different result when run twice. `"./ x"` is stored as `" x"` but replays as `"x"`, and `"./ /x"` fails on replay. The replay-after-write check and rollback catch this, but a crash in between would leave an event that can't replay.
4. The git-log history scan is permanent. One committed modify, delete or rename under the protected directories breaks preflight, append and converge for good. I couldn't check whether `.intent/decisions` was ever modified in `1a81914..83b35f7`; if it was, the gate is already broken.
5. `check_history_immutability` silently skips its check when `git rev-parse` fails, for example on a safe.directory error. Converge still fails closed in that case.
6. The schema and the runtime don't fully match, even though the docs claim "strict parity". The runtime strips whitespace from enum fields; the schema accepts a whitespace-only statement and doesn't enforce "seq 0 means prev is null".
7. A proposal with `note: null` gets the literal note "None" (`intent_gate.py:884`).
8. The CLI doesn't catch `yaml.YAMLError` from `_load_proposal`. It still exits nonzero, just with a traceback.
9. If `USER` is empty, `approve-evidence` writes an approval with an empty approver. From then on every principal manual PASS fails until someone deletes that file.

**VERDICT**

NOT READY, because of B1. After fixing it, run `python -m pytest tests/test_intent_gate.py` and `ctfh2 intent --root . check --require-cache` to confirm what I couldn't run here.

The same review and the fix plan are saved in `/home/yc54616/.claude/plans/review-the-current-working-zazzy-valiant.md`.
