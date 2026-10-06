BLOCKERS

- **Mixed root scopes produce false PASS** — [intent_gate.py:170](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:170). Reproduction: create and commit a ledger containing one agent requirement with `scope.paths: [".", "src"]`. Normalization drops the root marker, leaving `["src"]`. Calling `converge(root, paths=["docs"], execute_commands=False)` without evidence returns `PASS` with `results: []`, silently excluding the globally applicable requirement. Reproduced in a temporary Git repository.

- **Append retries overwrite or delete existing approvals** — [publication](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:966), [rollback](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:988). Reproduction: retain and commit a matching orphan approval, as permitted after interrupted publication. Retry the same approved proposal while mocking `write_active_cache` to raise `IntentGateError`. Rollback deletes the pre-existing committed approval; subsequent preflight rejects the history deletion. This reproduces with the same approver. Separately, a successful retry with another approver overwrites that committed approval and likewise breaks preflight.

NON_BLOCKING

- Trusting the local principal channel and ordinary Git history is explicitly acceptable for v0; malicious filesystem/history rewriting is outside its threat model.
- Semantic interpretation, split/merge/partial-scope supersession, and distributed approvals are explicitly deferred.

VERDICT

- NOT READY