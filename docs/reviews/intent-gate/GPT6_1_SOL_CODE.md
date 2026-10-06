BLOCKERS

- **Convergence can report PASS for an uncommitted implementation.** [intent_gate.py:1016](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:1016) checks cleanliness and captures the commit before executing verification commands, without rechecking afterward. Reproduced: a successful verification command changed a tracked file; convergence returned `PASS` stamped with the original commit. Revalidate cleanliness, commit, contract hash and ledger head before returning PASS.
- **Append bypasses accepted-history immutability.** [intent_gate.py:754](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:754) never calls `check_history_immutability`; [cli.py:102](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/cli.py:102) follows that path. Reproduced with a principal genesis and committed agent event: after rewriting the agent event, preflight rejected the history, but CLI append exited successfully and regenerated the cache from it. Enforce the existing history guard inside the append lock before publishing.

NON_BLOCKING

- All 24 existing tests and 16 supplemental invariant checks passed. Neither blocker has regression coverage; successful supersession and independent stale-head/commit rejection are also absent from the committed suite.
- Filesystem approval attestations and deferred RESTORE/split/merge behavior match the documented v0 limits.

VERDICT

- NOT READY