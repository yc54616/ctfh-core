BLOCKERS

- [CLI approval flow](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/cli.py:102): the principal types approval for one event hash, but [append_event](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:729) rereads the proposal and can approve a different event if it changes before append. The approved hash is never compared with the appended hash.
- [converge](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:1001): `status: passed, actor: principal` produces a principal manual PASS without any evidence field or proof of authorship. A read-only check produced `PASS` for all eight principal obligations with zero evidence fields.
- [Scope normalization](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:113): `.` becomes `""` inside `paths`, but [scope overlap](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:123) does not treat that as root. `--path .` can omit scoped requirements from convergence, and equivalent root scopes produce different `contract_hash` values.
- [Commit binding](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:926): convergence uses `HEAD` even with a dirty worktree, and uses the constant `NO_GIT_COMMIT` when no commit exists. Evidence can remain valid after the evaluated code changes, yielding a false PASS.
- [History immutability check](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:833): `git diff HEAD` detects only uncommitted edits. Once a rewritten or deleted last agent event is committed, preflight sees a clean tree and accepts the changed history, contrary to the append-only contract.
- [append_event](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:741): concurrent appends with the same proposal stem can both pass `exists()`. The later [os.replace](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:259) can overwrite an event after the first append reported success.
- [Event validation](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:350): replay accepts schema-invalid events. In particular, a missing `scope` becomes global and a missing `note` becomes empty, although both are required by [schema.json](/home/yc54616/Desktop/ctfh-core/.intent/schema.json:58); unexpected event fields are silently discarded.

NON_BLOCKING

- none

VERDICT

- NOT READY