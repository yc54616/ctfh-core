BLOCKERS

- **Approval is not bound to the appended candidate.** [CLI `_intent_main`](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/cli.py:102) confirms one hash, but [`append_event`](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:709) reloads the proposal and rebuilds the event, receiving only `confirmed=True`. A proposal edit during confirmation can therefore append a different principal change without approving its hash.

- **Multi-op events can silently overwrite newly created atoms.** [`_apply_event`](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:421) checks ID freshness only against the pre-event `all_atoms`. Multiple operations introducing the same fresh ID can overwrite each other in `new_all`/`new_active`, losing an obligation instead of rejecting the transition.

- **Root-path convergence can omit applicable obligations.** [`_normalize_path_prefix` / `_scope_overlap`](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:100) normalize `.` to `""`, but do not treat that prefix as overlapping descendants. Consequently, [`converge --path .`](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:946) skips scoped agent requirements and can return PASS without their evidence.

- **Event and contract canonicalization disagree.** [`normalize_atom` / `_binding_projection`](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:156) leave keys and verification references without NFC normalization, while `_event_hash` normalizes them. Equivalent Unicode keys were confirmed to produce identical event hashes but different `contract_hash` values. Collision checks also use these unnormalized keys.

- **Convergence can attribute PASS to an untested commit.** [`converge`](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:922) records `HEAD` but executes checks against the unchecked working tree. Dirty source changes neither invalidate previous evidence nor change the reported commit. `_git_commit` also permits the non-unique `NO_GIT_COMMIT` sentinel.

- **Interrupted principal appends can corrupt replay.** [`append_event`](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:736) publishes the event before its required approval. An interruption between those writes leaves an authoritative event that cannot replay; `except Exception` does not handle process termination or `KeyboardInterrupt`. Per-file atomic replacement does not make this publication atomic.

- **Concurrent appends can destroy accepted history.** [`_atomic_write`](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:259) and [`append_event`](/home/yc54616/Desktop/ctfh-core/src/ctfh_core/intent_gate.py:721) have no writer serialization or exclusive publication. Shared temporary filenames, overwriting replacement, and unconditional rollback deletion allow one append to overwrite or remove another writer’s successfully published event.

NON_BLOCKING

- Defer schema parity for decision-body fields: runtime requires them, while the schema does not conditionally require them.
- Full suite execution remains deferred; tests were inspected, and side-effect-free checks confirmed the Unicode and root-path findings. No files were edited.

VERDICT

- NOT READY